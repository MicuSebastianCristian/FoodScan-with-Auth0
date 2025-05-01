import json
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseRedirect, HttpResponseBadRequest
from .models import UserProfile, ScanHistory, IngredientInfo, CalorieEntry
import requests
import os
import logging
from jose import jwt
from jose.exceptions import JWTError
from django.contrib import messages # For showing success message
# Imports for barcode scanning
from PIL import Image as PILImage # Use alias to avoid conflict with models.ImageField if used later
from pyzbar import pyzbar
import cv2
from pyzbar.pyzbar import decode
import numpy as np
from django.utils import timezone # Needed for default dates
from datetime import timedelta # Needed for date calculations
from django.db.models import Sum, Avg # Needed for calculations
from django.views.decorators.http import require_POST # To ensure POST method
from django.core.paginator import Paginator # Import Paginator

# Import the Open Food Facts SDK
import openfoodfacts
# Import staticfiles finders
from django.contrib.staticfiles import finders
from django.core.files import File # Needed for demo scan
from io import BytesIO # Needed for demo scan

from .auth import get_login_url, get_logout_url, get_token_from_code
# Import the specific analyzers needed
from .services.ingredient_analyzer import analyze_ingredients, get_text_from_image_gemini

logger = logging.getLogger(__name__)

# --- Helper Functions ---

def _get_user_profile_data(user):
    """Fetches or creates UserProfile and returns preferences.

    Args:
        user: The Django User object.

    Returns:
        tuple: (allergens_dict, dietary_prefs_dict)
               Returns (None, None) if profile cannot be fetched/created.
    """
    if not user or not user.is_authenticated:
        return None, None
    try:
        profile, created = UserProfile.objects.get_or_create(user=user)
        if created:
            logger.info(f"Created UserProfile for user {user.username}")
        return profile.allergens, profile.dietary_preferences
    except Exception as e:
        logger.error(f"Error retrieving or creating profile for user {user.username}: {e}", exc_info=True)
        return None, None

def get_product_info_from_off(barcode):
    """Fetches product data using OFF SDK, with direct API fallback on None response."""
    logger.info(f"Querying OFF SDK for barcode: {barcode}")
    product_data = None # Initialize
    sdk_error = None

    # --- Attempt 1: Use SDK ---
    try:
        api = openfoodfacts.API(user_agent="FoodScanApp/1.0 (sebastian.micu112@gmail.com)")
        fields_to_get = [
            "code", "product_name", "product_name_en",
            "image_front_url", "image_url",
            "ingredients_text", "ingredients_text_en",
            "nutriments", "categories", "brands",
            "ecosystem" # Changed product_type to ecosystem based on common usage
        ]
        product_data = api.product.get(barcode, fields=fields_to_get)
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during OFF SDK request for {barcode}: {e}", exc_info=True)
        sdk_error = e # Store error to analyze later if needed
    except Exception as e:
        logger.error(f"Unexpected error using OFF SDK for {barcode}: {e}", exc_info=True)
        sdk_error = e # Store error

    # --- Process SDK Result (or proceed to fallback) ---
    if product_data is not None:
        # SDK returned data, process it
        product_type = product_data.get("ecosystem", "food")
        if product_type != "food":
             logger.warning(f"Product {barcode} found by OFF SDK, but is type '{product_type}'. Treating as wrong type.")
             return { # Return basic info but mark as wrong type
                 'found': False, 'reason': 'wrong_type', 'barcode': barcode,
                 'product_name': product_data.get('product_name_en') or product_data.get('product_name'),
                 'image_url': product_data.get('image_front_url') or product_data.get('image_url')
             }
        else: # Found and seems to be food
            logger.info(f"Product found via OFF SDK: {product_data.get('product_name', 'N/A')}")
            extracted_info = { # Extract data as before
                 'found': True, 'barcode': barcode,
                 'product_name': product_data.get('product_name_en') or product_data.get('product_name'),
                 'image_url': product_data.get('image_front_url') or product_data.get('image_url'),
                 'ingredients_text': product_data.get('ingredients_text_en') or product_data.get('ingredients_text'),
                 'nutriments': product_data.get('nutriments'),
                 'categories': product_data.get('categories'),
                 'brands': product_data.get('brands'),
             }
            return extracted_info

    # --- Fallback: SDK returned None or errored, try Direct API call ---
    else:
        logger.warning(f"OFF SDK returned None or error for {barcode}. Falling back to direct API call.")
        api_url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
        headers = {'User-Agent': 'FoodScanApp/1.0 (contact@yourapp.com) DirectFallback'}
        try:
            response = requests.get(api_url, headers=headers, timeout=15)
            logger.info(f"Direct API Fallback Status Code: {response.status_code}")
            logger.debug(f"Direct API Fallback Raw Response Text (first 500): {response.text[:500]}")

            if response.status_code == 404:
                try:
                    data = response.json()
                    # Check for the specific non-food product response
                    if data.get('status') == 0 and ('product type: beauty' in data.get('status_verbose', '')
                                                  or 'ecosystem": "beauty' in response.text): # Double check in raw text just in case
                        logger.warning(f"Product {barcode} confirmed as type 'beauty' via direct API fallback (Status 404).")
                        return {'found': False, 'barcode': barcode, 'reason': 'wrong_type'}
                    else:
                        logger.warning(f"Product {barcode} confirmed not found via direct API fallback (Status 404, standard reason).")
                        return {'found': False, 'barcode': barcode, 'reason': 'not_found'}
                except json.JSONDecodeError:
                    logger.warning(f"Product {barcode} not found via direct API fallback (Status 404, non-JSON response).")
                    return {'found': False, 'barcode': barcode, 'reason': 'not_found'}
            elif response.status_code == 200:
                 # This case is unlikely if SDK returned None, but handle defensively
                 logger.warning(f"Direct API fallback got 200 OK for {barcode} when SDK failed. Potential inconsistency.")
                 # Try to parse and return success? Or just mark as not found?
                 # Safest is to still treat as not found if SDK failed.
                 return {'found': False, 'barcode': barcode, 'reason': 'sdk_inconsistency'}
            else:
                # Other errors (5xx, etc.) from direct call
                logger.error(f"Direct API fallback failed with status {response.status_code} for {barcode}. Response: {response.text}")
                return None # Indicate API error

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during Direct API fallback for {barcode}: {e}", exc_info=True)
            return None # Indicate API/Network error
        except Exception as e:
            logger.error(f"Unexpected error during Direct API fallback for {barcode}: {e}", exc_info=True)
            return None

def _prepare_scan_result_context(scan_entry):
    """Prepares the context dictionary needed to render the scan_result template.

    Args:
        scan_entry: A ScanHistory object.

    Returns:
        dict: A context dictionary containing formatted results, score info, etc.
              Returns an empty dict if scan_entry is invalid.
    """
    if not scan_entry:
        logger.error("_prepare_scan_result_context called with invalid scan_entry.")
        return {}

    # Safely parse analysis results JSON
    try:
        analysis_result_dict_for_template = json.loads(scan_entry.analysis_result) if scan_entry.analysis_result else {}
        analysis_result_dict_for_template.pop('nutritional_data', None) # Exclude raw nutritional data
    except json.JSONDecodeError:
        logger.error(f"Error decoding analysis_result JSON from scan_entry {scan_entry.id} for template context.")
        analysis_result_dict_for_template = {}

    # Calculate SVG circle offset for health score visualization
    score = scan_entry.health_score
    total_circumference = 376.8 # Circumference of the SVG circle
    score_for_svg = max(0, min(100, score))
    stroke_dashoffset = total_circumference * (1 - (score_for_svg / 100))

    # Determine Health Score Color
    score_color_class = 'text-success'
    score_stroke_color = '#4CAF50'
    if score < 40:
        score_color_class = 'text-danger'
        score_stroke_color = '#dc3545'
    elif score < 70:
        score_color_class = 'text-warning'
        score_stroke_color = '#ffc107'

    # Process and format nutritional data stored in the scan entry
    nutritional_data_dict = scan_entry.nutritional_data
    if isinstance(nutritional_data_dict, str):
        try:
            nutritional_data_dict = json.loads(nutritional_data_dict)
        except json.JSONDecodeError:
            logger.error(f"Could not decode nutritional_data JSON string from scan_entry {scan_entry.id}")
            nutritional_data_dict = {}
    elif not isinstance(nutritional_data_dict, dict):
        logger.warning(f"scan_entry.nutritional_data for ID {scan_entry.id} is not a dict or valid JSON string. Type: {type(nutritional_data_dict)}")
        nutritional_data_dict = {}

    formatted_nutrients = []
    # Defines the structure and formatting rules for displaying nutrients.
    nutrient_map = [
        {'key': 'energy-kcal_100g', 'label': 'Energy (kcal)', 'unit': 'kcal', 'format': ':.0f', 'fallback_key': 'energy-kj_100g', 'fallback_label': 'Energy (kJ)', 'fallback_unit': 'kJ'},
        {'key': 'fat_100g', 'label': 'Fat', 'unit': 'g', 'format': ':.1f', 'sub_key': 'saturated-fat_100g', 'sub_label': 'saturates'},
        {'key': 'carbohydrates_100g', 'label': 'Carbohydrates', 'unit': 'g', 'format': ':.1f', 'sub_key': 'sugars_100g', 'sub_label': 'sugars'},
        {'key': 'fiber_100g', 'label': 'Fiber', 'unit': 'g', 'format': ':.1f'},
        {'key': 'proteins_100g', 'label': 'Proteins', 'unit': 'g', 'format': ':.1f'},
        {'key': 'salt_100g', 'label': 'Salt', 'unit': 'g', 'format': ':.2f', 'sub_key': 'sodium_100g', 'sub_label': 'Sodium'},
    ]

    for item in nutrient_map:
        value = nutritional_data_dict.get(item['key'])
        label = item['label']
        unit = item['unit']

        # Handle energy fallback (kcal -> kJ)
        if item['key'] == 'energy-kcal_100g' and value is None:
            value = nutritional_data_dict.get(item['fallback_key'])
            if value is not None:
                label = item['fallback_label']
                unit = item['fallback_unit']

        if value is not None:
            try:
                format_spec = item.get('format', ':.1f').lstrip(':')
                formatted_value = format(float(value), format_spec)
                nutrient_entry = {'label': label, 'value': formatted_value, 'unit': unit}

                # Handle sub-values (saturates, sugars, sodium)
                if 'sub_key' in item:
                    sub_value = nutritional_data_dict.get(item['sub_key'])
                    sub_label = item['sub_label']
                    if sub_value is not None:
                        try:
                            formatted_sub_value = format(float(sub_value), format_spec)
                            nutrient_entry['sub_info'] = f"(of which {sub_label} {formatted_sub_value}{unit})"
                        except (ValueError, TypeError):
                            nutrient_entry['sub_info'] = f"(of which {sub_label} ?{unit})"
                    else:
                        nutrient_entry['sub_info'] = f"(of which {sub_label} ?{unit})"

                formatted_nutrients.append(nutrient_entry)

            except (ValueError, TypeError) as format_err:
                logger.warning(f"Could not format nutrient value for key {item['key']}: {value}. Error: {format_err}")

    # Prepare nutritional data JSON for debugging output in the template
    debug_nutritional_json = None
    if nutritional_data_dict:
        try:
            debug_nutritional_json = json.dumps(nutritional_data_dict, indent=2)
        except TypeError as json_err:
            logger.error(f"Could not serialize nutritional_data_dict for debugging: {json_err}")

    # Construct the final context dictionary
    context = {
        'scan_id': scan_entry.id,
        'result': analysis_result_dict_for_template,
        'stroke_dashoffset': stroke_dashoffset,
        'product_name': scan_entry.product_name,
        'product_image_url': scan_entry.product_image_url,
        'barcode': scan_entry.barcode,
        'ingredients_missing_from_off': scan_entry.source == 'barcode_off_no_ingredients', # Check source for this flag
        'formatted_nutrients': formatted_nutrients,
        'debug_nutritional_json': debug_nutritional_json,
        'score_color_class': score_color_class,
        'score_stroke_color': score_stroke_color
    }
    return context

def _handle_barcode_scan(barcode_image_file):
    """Processes a barcode image file.

    Saves temp file, reads barcode, queries Open Food Facts (with fallback).

    Args:
        barcode_image_file: UploadedFile object for the barcode image.

    Returns:
        tuple: (barcode_value, product_info, extracted_text, nutritional_data, ingredients_missing, error_message)
               Returns mostly None values and an error message if processing fails.
    """
    temp_img_path = None
    barcode_value = None
    product_info = None
    extracted_text = "" # Default to empty string
    nutritional_data = {}
    ingredients_missing = True # Default to true unless OFF provides text
    error_message = None

    try:
        # Save temporary image file
        temp_img_path = os.path.join(settings.MEDIA_ROOT, 'temp', barcode_image_file.name)
        os.makedirs(os.path.dirname(temp_img_path), exist_ok=True)
        with open(temp_img_path, 'wb+') as destination:
            for chunk in barcode_image_file.chunks(): destination.write(chunk)

        # Read barcode from the saved image
        barcode_value = read_barcode_from_image(temp_img_path)

        if barcode_value:
            logger.info(f"Barcode detected: {barcode_value}")
            # Query Open Food Facts (uses SDK with direct API fallback)
            product_info = get_product_info_from_off(barcode_value)

            if product_info and product_info.get('found'):
                logger.info(f"Found product data via OFF for barcode: {barcode_value}")
                off_ingredients_text = product_info.get('ingredients_text')
                # Store nutritional data from OFF
                nutritional_data = product_info.get('nutriments', {})

                if off_ingredients_text and len(off_ingredients_text) > 5:
                    extracted_text = off_ingredients_text
                    ingredients_missing = False
                    logger.info(f"Using OFF ingredients text for barcode: {barcode_value}")
                else:
                    logger.warning(f"OFF product found but no ingredients text for barcode: {barcode_value}")
                    # ingredients_missing remains True, extracted_text remains ""
            else:
                # OFF did not find the product or it was wrong type
                reason = product_info.get('reason', 'not_found') if product_info else 'not_found'
                logger.warning(f"No product found via OFF for barcode: {barcode_value} (Reason: {reason})")
                # ingredients_missing remains True, extracted_text remains ""
        else:
            logger.warning("No barcode detected in image.")
            error_message = "Could not detect a barcode in the uploaded image."

    except Exception as e:
        logger.error(f"Error processing barcode image: {e}", exc_info=True)
        error_message = "An error occurred while processing the barcode image."
        # Reset values on error
        barcode_value = None
        product_info = None
        extracted_text = ""
        nutritional_data = {}
        ingredients_missing = True

    finally:
        # Clean up temporary image file
        if temp_img_path and os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
                logger.debug(f"Removed temp barcode image: {temp_img_path}")
            except OSError as e:
                logger.error(f"Error removing temp file {temp_img_path}: {e}")

    return barcode_value, product_info, extracted_text, nutritional_data, ingredients_missing, error_message

def _handle_ocr_scan(ingredients_image_file):
    """Processes an ingredients image file using Gemini Vision OCR.

    Saves temp file, calls Gemini OCR service, cleans up temp file.

    Args:
        ingredients_image_file: UploadedFile object for the ingredients image.

    Returns:
        tuple: (extracted_text, error_message)
               Returns (None, error_message) if processing fails.
    """
    temp_img_path = None
    extracted_text = None
    error_message = None
    logger.info("Processing uploaded ingredients image (Gemini OCR)." )

    try:
        # Save temporary image file
        temp_img_path = os.path.join(settings.MEDIA_ROOT, 'temp', ingredients_image_file.name)
        os.makedirs(os.path.dirname(temp_img_path), exist_ok=True)
        with open(temp_img_path, 'wb+') as destination:
            for chunk in ingredients_image_file.chunks(): destination.write(chunk)

        # Call Gemini Vision OCR service
        logger.info(f"Calling Gemini Vision OCR for image: {temp_img_path}")
        extracted_text = get_text_from_image_gemini(temp_img_path)

        if extracted_text is None: # Check for API error
            logger.error("Gemini Vision OCR failed (returned None).")
            error_message = "Text recognition failed. Please try again or paste text."
            extracted_text = None # Ensure text is None on API failure
        elif not extracted_text: # Check for empty string (no text found)
            logger.warning("Gemini Vision OCR found no text in the image.")
            error_message = "Could not find any text in the uploaded image. Please try again or paste text."
            extracted_text = None # Treat no text found as an error for scan flow
        else:
            # Text successfully extracted
            logger.info(f"Successfully extracted text via Gemini OCR (length: {len(extracted_text)}).")

    except Exception as e:
        logger.error(f"Error processing ingredients image (Gemini OCR): {e}", exc_info=True)
        error_message = "An error occurred while processing the ingredients image."
        extracted_text = None # Ensure text is None on exception

    finally:
        # Clean up temporary image file
        if temp_img_path and os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
                logger.debug(f"Removed temp OCR image: {temp_img_path}")
            except OSError as e:
                logger.error(f"Error removing temp file {temp_img_path}: {e}")

    return extracted_text, error_message

def _handle_text_scan(text_input, source_type_hint):
    """Handles direct text input or edited text.

    Args:
        text_input (str): The text provided by the user.
        source_type_hint (str): A hint for logging ('edited' or 'direct').

    Returns:
        tuple: (extracted_text, source_type)
    """
    logger.info(f"Processing scan based on {source_type_hint} text input.")
    # Basic sanitization could be added here if needed (e.g., strip whitespace)
    extracted_text = text_input.strip() if text_input else ""
    # Determine source type based on hint
    source_type = 'edited_text' if source_type_hint == 'edited' else 'direct_text'
    return extracted_text, source_type

def callback(request):
    token = get_token_from_code(request)
    redirect_to = reverse("home") # Default redirect
    if token:
        request.session["user"] = token
        # Check if there's a 'next' parameter from login_required or state
        # Authlib might store original URL in session state, check documentation if needed
        # Simple check for 'next' query param first
        next_url = request.GET.get('next', None)
        if next_url:
            # Basic security check: Ensure next_url is relative or on the same host?
            # For simplicity now, we assume it's safe if present.
            redirect_to = next_url

        return HttpResponseRedirect(redirect_to) # Use HttpResponseRedirect

    # If no token, redirect to home (or maybe login with error? Consider adding error message)
    return HttpResponseRedirect(redirect_to)

def login(request):
    return get_login_url(request)

def logout(request):
    request.session.clear()
    return redirect(get_logout_url(request))

def index_view(request):
    """Renders the main landing page."""
    # If user is already logged in, redirect to scan page
    if request.user.is_authenticated:
        return redirect('scan')
    # Otherwise, show the landing page
    return render(request, 'foodscan/index.html')

def home(request):
    # This view renders a basic home page for authenticated users.
    # It might be a candidate for removal or conversion into a dashboard.
    if not request.user.is_authenticated:
         return redirect('index_view') # Redirect non-logged-in users to the landing page

    # The auth0_session context processor makes session data available.
    return render(
        request,
        "foodscan/home.html",
        context={
            "pretty": json.dumps(request.session.get("user"), indent=4),
        },
    )

@login_required
def scan(request):
    """Handles the main scanning functionality.

    GET: Renders the scan form page.
    POST: Processes various inputs (barcode image, ingredients image, text input)
          to initiate a new scan or update an existing one. Interacts with
          barcode decoding, OCR services (Gemini Vision), Open Food Facts,
          and the ingredient analysis service.
    """
    if request.method == 'POST':
        # Move the import here to be available for both new scans and updates
        # (analyze_ingredients already imported above)
        # from .services.ingredient_analyzer import analyze_ingredients

        # === Check for Demo Scan Mode ===
        scan_mode = request.POST.get('scan_mode', 'upload') # Default to 'upload'
        demo_image_path = None
        demo_image_file_obj = None

        if scan_mode == 'demo':
            logger.info("Processing demo scan request.")
            # === TEMPORARY DEBUGGING: Hardcode absolute path ===
            # Find the absolute path to the demo image in static files
            # relative_demo_path = 'foodscan/images/demo_barcode.jpg'
            # demo_image_path = finders.find(relative_demo_path)
            demo_image_path = '/home/bejerital/foodscan/static/foodscan/images/demo_barcode.jpg'
            logger.info(f"DEBUG: Using hardcoded demo image path: {demo_image_path}")
            # =====================================================

            if not demo_image_path or not os.path.exists(demo_image_path):
                logger.error(f"Demo barcode image not found at HARDCODED path: {demo_image_path}") # Modified log message
                messages.error(request, "Could not find the demo scan image. Please contact support.")
                return redirect('scan')
            else:
                 logger.info(f"Found demo barcode image at: {demo_image_path}")
                 # Create a File object from the path for compatibility with _handle_barcode_scan
                 try:
                     # We need to pass a file-like object to _handle_barcode_scan
                     # Read the demo image into memory and create a File object
                     with open(demo_image_path, 'rb') as f:
                         # Use BytesIO to hold the content in memory
                         in_memory_file = BytesIO(f.read())
                         # Create a Django File object
                         # Use the original filename for consistency if needed by _handle_barcode_scan
                         demo_image_file_obj = File(in_memory_file, name=os.path.basename(demo_image_path))
                 except Exception as e:
                      logger.error(f"Error creating file object for demo image {demo_image_path}: {e}", exc_info=True)
                      messages.error(request, "An error occurred preparing the demo scan image.")
                      return redirect('scan')

        # Check if this is an update request for an existing scan
        original_scan_id = request.POST.get('original_scan_id')

        # Get uploaded files (or None if demo mode)
        barcode_image_file = request.FILES.get('barcode_image') if scan_mode == 'upload' else None
        ingredients_image_file = request.FILES.get('image') # For OCR (original or update)
        edited_text = request.POST.get('edited_text')
        direct_text_input = request.POST.get('text_input')

        extracted_text = None
        source_type = 'unknown'
        product_info = None
        barcode_value = None
        temp_img_path = None
        scan_entry = None # Will hold the ScanHistory object (new or existing)
        ingredients_missing = False

        # --- Scenario 0: Updating an existing scan ---
        if original_scan_id:
            logger.info(f"Processing ingredients update for existing ScanHistory ID: {original_scan_id}")
            scan_entry = get_object_or_404(ScanHistory, id=original_scan_id, user=request.user)

            pasted_text_input = request.POST.get('text_input')
            ingredients_image_file = request.FILES.get('image') # Check again for image update
            update_successful = False # Flag to track if update input was processed

            # --- Option A: Update via Pasted Text ---
            if pasted_text_input:
                extracted_text, source_type = _handle_text_scan(pasted_text_input, 'edited')
                scan_entry.image = None # No image associated with text update
                logger.info(f"Updating scan {original_scan_id} using pasted text.")
                update_successful = True

            # --- Option B: Update via Uploaded Image (OCR using Gemini) ---
            elif ingredients_image_file:
                source_type = 'barcode_ocr' # Or maybe 'updated_ocr_gemini'?
                logger.info(f"Updating scan {original_scan_id} using uploaded ingredients image (Gemini OCR)." )
                temp_img_path = None # Reset for safety
                try:
                    # Save temp image (same as before)
                    temp_img_path = os.path.join(settings.MEDIA_ROOT, 'temp', ingredients_image_file.name)
                    os.makedirs(os.path.dirname(temp_img_path), exist_ok=True)
                    with open(temp_img_path, 'wb+') as destination:
                        for chunk in ingredients_image_file.chunks(): destination.write(chunk)

                    # === Call Gemini for OCR ===
                    logger.info(f"Calling Gemini Vision OCR for image: {temp_img_path}")
                    extracted_text = get_text_from_image_gemini(temp_img_path)
                    # ==========================

                    if extracted_text is None: # Check for API error from Gemini
                        logger.error(f"Gemini Vision OCR failed for update scan {original_scan_id} (returned None).")
                        messages.error(request, "Text recognition failed using the new service. Please try pasting text or a different image.")
                        # Decide if we should redirect here or allow proceeding without text?
                        # Redirecting seems safer if OCR failed completely.
                        return redirect('scan')
                    elif not extracted_text: # Check for empty string (API ok, but no text found)
                         logger.warning(f"Gemini Vision OCR found no text in image for update scan {original_scan_id}.")
                         messages.error(request, "Could not find any text in the uploaded image. Please try pasting text or a different image.")
                         return redirect('scan')
                    else:
                         # Successfully extracted text via Gemini OCR
                         logger.info(f"Successfully extracted text via Gemini OCR for updating scan {original_scan_id}")
                         if ingredients_image_file and hasattr(ingredients_image_file, 'name'):
                             scan_entry.image = ingredients_image_file.name
                         else:
                             logger.error(f"Internal inconsistency: ingredients_image_file object issue for scan {original_scan_id}.")
                             scan_entry.image = None
                         update_successful = True # Mark update input as processed

                except Exception as e: # Catch any unexpected error during file handling or Gemini call
                    logger.error(f"Error processing ingredients image (Gemini OCR) for update scan {original_scan_id}: {e}", exc_info=True)
                    messages.error(request, "An error occurred while processing the ingredients image.")
                    return redirect('scan')
                finally:
                     if temp_img_path and os.path.exists(temp_img_path):
                         try: os.remove(temp_img_path)
                         except OSError as e: logger.error(f"Error removing temp file {temp_img_path}: {e}")

            # --- Option C: Update via Edited Text from Results Page ---
            elif edited_text:
                extracted_text, source_type = _handle_text_scan(edited_text, 'edited')
                scan_entry.image = None  # No image associated with edited text update
                logger.info(f"Updating scan {original_scan_id} using edited text from results page.")
                update_successful = True

            # --- Analysis & Update Part for Scenario 0 (Only if update input was processed) ---
            if update_successful and extracted_text is not None:
                logger.info(f"Running analysis service for updated scan {original_scan_id}...")
                # Get original product info for context if needed by analyzer
                product_info = { 'found': True, 'product_name': scan_entry.product_name, 'image_url': scan_entry.product_image_url, 'nutriments': scan_entry.nutritional_data }
                barcode_value = scan_entry.barcode

                # Fetch user preferences using helper function
                user_allergens, user_dietary_prefs = _get_user_profile_data(request.user)

                # Prepare nutritional data (might be JSON string in older entries)
                nutritional_data_for_analysis = product_info.get('nutriments')
                if isinstance(nutritional_data_for_analysis, str):
                    try:
                        nutritional_data_for_analysis = json.loads(nutritional_data_for_analysis)
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse nutritional_data JSON string for scan {original_scan_id}")
                        nutritional_data_for_analysis = None
                elif not isinstance(nutritional_data_for_analysis, dict):
                    nutritional_data_for_analysis = None

                # Call the main analysis service
                analysis_result = analyze_ingredients(
                    extracted_text,
                    user_allergens=user_allergens,
                    user_dietary_prefs=user_dietary_prefs, # <-- Pass dietary prefs
                    nutritional_data=nutritional_data_for_analysis
                )
                score = analysis_result.get('score', 50)
                final_nutritional_data_from_analyzer = analysis_result.get('nutritional_data', {})

                # Update the existing ScanHistory entry with new analysis results
                scan_entry.extracted_text = extracted_text
                scan_entry.analysis_result = json.dumps(analysis_result if analysis_result else {})
                scan_entry.health_score = score
                scan_entry.source = source_type
                scan_entry.nutritional_data = final_nutritional_data_from_analyzer
                scan_entry.save()
                logger.info(f"Successfully updated ScanHistory entry ID: {scan_entry.id}")

                # Redirect to scan_result to show the updated results
                return redirect('scan_result', scan_id=scan_entry.id)

            elif update_successful and extracted_text is None:
                # This means OCR failed but we didn't redirect (should not happen with current logic)
                logger.error(f"Update logic error for scan {original_scan_id}: update_successful but extracted_text is None.")
                messages.error(request, "Failed to process ingredients data after update attempt.")
                return redirect('scan')

        # --- Priority 1: Barcode Image (New Scan - Upload or Demo) ---
        elif barcode_image_file or scan_mode == 'demo':
            source_type = 'barcode_attempt' # Initial source type
            logger.info(f"Processing scan based on barcode image (Mode: {scan_mode}).")

            # Use the demo file object if in demo mode, otherwise the uploaded file
            input_barcode_file = demo_image_file_obj if scan_mode == 'demo' else barcode_image_file

            if not input_barcode_file:
                 # Should not happen if logic is correct, but good to check
                 logger.error(f"Internal error: No barcode input file found for scan mode '{scan_mode}'.")
                 messages.error(request, "An internal error occurred processing the barcode input.")
                 return redirect('scan')

            # Call helper function to handle barcode processing
            barcode_value, product_info, extracted_text, nutritional_data_from_off, ingredients_missing, error_message = \
                _handle_barcode_scan(input_barcode_file)

            if error_message:
                 messages.error(request, error_message)
                 return redirect('scan')

            # Determine final source type based on helper results
            if barcode_value:
                if product_info and product_info.get('found'):
                    if not ingredients_missing:
                         source_type = 'barcode_off_success'
                    else:
                         source_type = 'barcode_off_no_ingredients'
                else:
                     source_type = 'barcode_no_off_match'
            # else: source_type remains 'barcode_attempt' (but error message should have triggered redirect)

        # --- Priority 2: Edited Text (New Scan) ---
        elif edited_text is not None and edited_text.strip():
            extracted_text = edited_text
            source_type = 'edited_text'
            logger.info("Processing scan based on edited text from results page.")

        # --- Priority 3: Direct Text Input --- (No change needed)
        elif direct_text_input:
            extracted_text = direct_text_input
            source_type = 'direct_text'
            logger.info("Processing scan based on direct text input from scan page.")

        # --- Priority 4: Ingredients Image (OCR using Gemini - New Scan) ---
        elif ingredients_image_file:
            source_type = 'ocr_gemini'

            # Call helper function to handle OCR processing
            extracted_text, error_message = _handle_ocr_scan(ingredients_image_file)

            if error_message:
                 messages.error(request, error_message)
                 return redirect('scan')

            # Note: product_info and barcode_value will be None for OCR-only scans
            nutritional_data_from_off = {} # No OFF data for OCR scan
            ingredients_missing = False # Assume text was found if no error

        # --- No valid input provided ---
        else:
             logger.warning("Scan request received without barcode image, ingredients image, edited text, or direct text input.")
             messages.error(request, "Please provide input: Scan a barcode, upload ingredients image, or paste text.")
             return redirect('scan')

        # --- Common logic: Analyze extracted_text (if available for a NEW scan) ---
        # This block runs only if scan_entry is None (i.e., not an update)
        # and some text source was successfully processed.
        if scan_entry is None and extracted_text is not None:
            logger.info(f"Running analysis service for new scan (Source: {source_type})...")

            # Fetch user preferences using helper function
            user_allergens, user_dietary_prefs = _get_user_profile_data(request.user)

            # Prepare nutritional data (might have come from OFF if barcode was used)
            nutritional_data_for_analysis = product_info.get('nutriments') if product_info else None
            if isinstance(nutritional_data_for_analysis, str):
                try:
                    nutritional_data_for_analysis = json.loads(nutritional_data_for_analysis)
                except json.JSONDecodeError:
                    nutritional_data_for_analysis = None
            elif not isinstance(nutritional_data_for_analysis, dict):
                nutritional_data_for_analysis = None

            # Analyze ingredients using the potentially Gemini-extracted text
            analysis_result = analyze_ingredients(
                extracted_text,
                user_allergens=user_allergens,
                user_dietary_prefs=user_dietary_prefs, # <-- Pass dietary prefs
                nutritional_data=nutritional_data_for_analysis # Pass initial data
            )
            score = analysis_result.get('score', 50)
            final_nutritional_data_from_analyzer = analysis_result.get('nutritional_data', {})

            # Calculate SVG circle offset (using the final score)
            total_circumference = 376.8
            score_for_svg = max(0, min(100, score))
            stroke_dashoffset = total_circumference * (1 - (score_for_svg / 100))

            # --- Determine Health Score Color ---
            score_color_class = 'text-success' # Default green
            score_stroke_color = '#4CAF50' # Default green
            if score < 40:
                score_color_class = 'text-danger'
                score_stroke_color = '#dc3545' # Bootstrap danger red
            elif score < 70:
                score_color_class = 'text-warning'
                score_stroke_color = '#ffc107' # Bootstrap warning yellow

            # --- Save or Update scan history ---
            if request.user.is_authenticated:
                profile = UserProfile.objects.filter(user=request.user).first()
                if profile:
                    if scan_entry: # If we are updating (Scenario 0)
                        # Update fields using the results from analyze_ingredients
                        scan_entry.analysis_result = json.dumps(analysis_result if analysis_result else {})
                        scan_entry.health_score = score
                        # --- Crucially, update nutritional_data with the FINAL combined data ---
                        scan_entry.nutritional_data = final_nutritional_data_from_analyzer
                        # Source and extracted_text were set during the update logic earlier
                        # scan_entry.image might have been updated earlier too
                        scan_entry.save()
                        logger.info(f"Final update save for ScanHistory ID: {scan_entry.id}")
                    else: # Create a new entry for non-update scenarios
                        scan_entry_data = {
                            'user': request.user,
                            'extracted_text': extracted_text,
                            'health_score': score,
                            'analysis_result': json.dumps(analysis_result if analysis_result else {}),
                            'source': source_type,
                            'barcode': barcode_value,
                            # --- Use the FINAL combined nutritional data here too ---
                            'nutritional_data': final_nutritional_data_from_analyzer
                        }
                        # Add product info only if it came from OFF initially
                        if source_type == 'barcode_off_success' and product_info and product_info.get('found'):
                            scan_entry_data['product_name'] = product_info.get('product_name')
                            scan_entry_data['product_image_url'] = product_info.get('image_url')
                            # nutritional_data is already handled above

                        # Set image field only if source was OCR (for a *new* scan)
                        if source_type == 'ocr_gemini' and ingredients_image_file:
                            scan_entry_data['image'] = ingredients_image_file.name
                        else:
                            scan_entry_data['image'] = None

                        try:
                            scan_entry = ScanHistory(**scan_entry_data)
                            scan_entry.save()
                            logger.info(f"New ScanHistory entry saved (ID: {scan_entry.id}, Source: {source_type}) - Nutritional Data: {final_nutritional_data_from_analyzer}")
                        except Exception as e:
                             logger.error(f"Failed to save new ScanHistory: {e}", exc_info=True)
                             scan_entry = None # Ensure scan_entry is None if save fails
                else:
                     logger.error(f"Could not find/create profile for user {request.user.username} when saving history.")

            # --- Prepare context for template using the final scan_entry ---
            if not scan_entry:
                # Handle case where saving failed or user not authenticated properly
                messages.error(request, "Failed to save or retrieve scan results.")
                return redirect('scan')

            # Use the helper function to prepare context for the results page
            context = _prepare_scan_result_context(scan_entry)

            # Add the ingredients_missing flag specifically determined during barcode processing
            # This isn't part of the standard scan_entry data used by the helper.
            context['ingredients_missing_from_off'] = ingredients_missing

            return render(request, 'foodscan/scan_result.html', context)

    # GET request: Render the scan page
    return render(request, 'foodscan/scan.html')

@login_required
def history(request):
    """Displays the user's scan history with search and pagination.

    GET parameters:
        scan_id: Filter by exact ScanHistory ID.
        scan_date: Filter by scan creation date (YYYY-MM-DD).
        page: Page number for pagination.
    """
    # Get search query and date from GET parameters
    search_query = request.GET.get('scan_id', '').strip()
    scan_date_str = request.GET.get('scan_date', '').strip() # Get the date string

    # Initial queryset
    scans_list = ScanHistory.objects.filter(user=request.user).order_by('-created_at')

    # Filter by Scan ID if provided
    if search_query:
        scans_list = scans_list.filter(id__iexact=search_query) # Case-insensitive exact match

    # Filter by Date if provided and valid
    scan_date = None
    if scan_date_str:
        try:
            # Convert the string to a date object
            scan_date = timezone.datetime.strptime(scan_date_str, '%Y-%m-%d').date()
            # Filter for scans created on that specific date
            scans_list = scans_list.filter(created_at__date=scan_date)
        except ValueError:
            # Invalid date format, ignore the date filter or show an error?
            messages.warning(request, "Invalid date format. Please use YYYY-MM-DD.")
            # Optionally, redirect or show all results if date is invalid
            # For now, we'll just ignore the invalid date and show results filtered by ID (if any)
            pass

    # Paginator logic
    paginator = Paginator(scans_list, 15) # Show 15 scans per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'scans': page_obj, # Pass the Page object to the template
        'search_query': search_query,
        'scan_date': scan_date_str, # Pass the original date string back for the form
        'page_obj': page_obj # Also pass page_obj for pagination controls
    }
    return render(request, 'foodscan/history.html', context)

@login_required
def profile(request):
    """Displays and handles updates for the user's profile settings.

    GET: Renders the profile form, showing current preferences and
         (for debugging) unverified decoded Auth0 tokens.
    POST: Updates dietary preferences, allergens, and calorie goal based on
          form submission.
    """
    profile, created = UserProfile.objects.get_or_create(user=request.user)
    auth0_user_data = request.session.get('user', {})
    user_info = auth0_user_data.get('userinfo', {})
    id_token = auth0_user_data.get('id_token')
    access_token = auth0_user_data.get('access_token')
    decoded_id_token = None
    decoded_access_token = None
    jwt_error = None
    try:
        if id_token:
            decoded_id_token = jwt.get_unverified_claims(id_token)
        if access_token:
             try:
                 decoded_access_token = jwt.get_unverified_claims(access_token)
             except JWTError:
                 decoded_access_token = None
                 logger.info("Access token is not a JWT, cannot decode claims.")
             except Exception as decode_err:
                  decoded_access_token = None
                  logger.error(f"Error decoding access token: {decode_err}")
    except JWTError as e:
        logger.error(f"Error decoding ID token: {e}")
        jwt_error = str(e)
    except Exception as e:
        logger.error(f"Unexpected error during token decoding: {e}")
        jwt_error = "An unexpected error occurred during token decoding."

    if request.method == 'POST':
        # Handle Dietary Preferences
        dietary_prefs = {
            'vegetarian': request.POST.get('vegetarian') == 'on',
            'vegan': request.POST.get('vegan') == 'on',
            'gluten_free': request.POST.get('gluten_free') == 'on',
            'sugar_free': request.POST.get('sugar_free') == 'on',
        }
        profile.dietary_preferences = dietary_prefs

        # Handle Allergens
        standard_allergens = request.POST.getlist('allergens') # Gets list of values from checked boxes
        custom_allergen = request.POST.get('custom_allergen', '').strip()

        profile.allergens = {
            'standard': standard_allergens,
            'custom': custom_allergen
        }

        # --- Handle Daily Calorie Goal ---
        goal_input = request.POST.get('daily_calorie_goal', '').strip()
        if goal_input:
            try:
                profile.daily_calorie_goal = int(goal_input)
                # Basic validation: Ensure it's a positive number (or handle zero if allowed)
                if profile.daily_calorie_goal <= 0:
                    profile.daily_calorie_goal = None # Or raise validation error / show message
                    messages.warning(request, 'Daily calorie goal must be a positive number.')
            except (ValueError, TypeError):
                profile.daily_calorie_goal = None # Clear if input is invalid
                messages.error(request, 'Invalid input for daily calorie goal. Please enter a number.')
        else:
            profile.daily_calorie_goal = None # Clear goal if input is empty

        profile.save()
        # Only show success if no errors occurred during goal setting
        if not any(message.level == messages.ERROR or message.level == messages.WARNING for message in messages.get_messages(request)):
             messages.success(request, 'Preferences saved successfully!')
        return redirect('profile')

    # GET request: Prepare context for template

    # Prepare dietary options with labels and checked status
    standard_dietary_keys = ['vegetarian', 'vegan', 'gluten_free', 'sugar_free']
    dietary_options_with_labels = []
    current_prefs = profile.dietary_preferences # Get the dict like {'vegetarian': True, ...}
    for key in standard_dietary_keys:
        label = key.replace('_', ' ').capitalize()
        # Safely get boolean value from the profile preferences dict
        is_checked = current_prefs.get(key, False)
        dietary_options_with_labels.append({
            'key': key,
            'label': label,
            'checked': is_checked # Add the boolean status
        })

    # Prepare allergen options with labels (simple capitalization here)
    standard_allergen_keys = ['nuts', 'dairy', 'eggs', 'soy', 'wheat']
    allergen_options_with_labels = []
    for key in standard_allergen_keys:
         allergen_options_with_labels.append({'key': key, 'label': key.capitalize()})

    context = {
        'profile': profile,
        'auth0_user_id': user_info.get('sub'),
        'id_token': id_token,
        'access_token': access_token,
        'decoded_id_token': json.dumps(decoded_id_token, indent=2) if decoded_id_token else None,
        'decoded_access_token': json.dumps(decoded_access_token, indent=2) if decoded_access_token else "(Not a JWT or decode error)",
        'jwt_error': jwt_error,
        'dietary_options': dietary_options_with_labels,
        'allergen_options': allergen_options_with_labels,
        # Pass the current goal to the template
        'current_calorie_goal': profile.daily_calorie_goal
    }

    return render(request, 'foodscan/profile.html', context)

@login_required
def ingredient_info(request, ingredient_name):
    """Displays information about a specific ingredient.

    Retrieves data from the IngredientInfo model.
    (Assumes IngredientInfo model is populated elsewhere).
    """
    try:
        ingredient = IngredientInfo.objects.get(name=ingredient_name)
        # Context processor handles session
        return render(request, 'foodscan/ingredient_info.html', {'ingredient': ingredient})
    except IngredientInfo.DoesNotExist:
        # Context processor handles session
        return render(request, 'foodscan/ingredient_info.html', {'error': 'Ingredient not found'})

@login_required
def scan_result(request, scan_id):
    """Displays the results of a previously completed scan.

    Retrieves a ScanHistory entry by ID and prepares the context
    for rendering the results page, similar to the end of the scan view's POST.
    """
    # Retrieve the scan entry by ID, ensuring it belongs to the current user
    scan_entry = get_object_or_404(ScanHistory, id=scan_id, user=request.user)

    # Use the helper function to prepare the display context
    context = _prepare_scan_result_context(scan_entry)

    # Check if context preparation failed (helper returns empty dict)
    if not context:
         messages.error(request, "Could not prepare results display for this scan.")
         return redirect('history') # Redirect to history if context fails

    return render(request, 'foodscan/scan_result.html', context)

def read_barcode_from_image(image_path):
    """Reads a barcode from an image file using pyzbar and OpenCV.

    Attempts multiple preprocessing steps (grayscale, thresholding, blur)
    to improve detection success rate.

    Args:
        image_path: Path to the image file.

    Returns:
        The decoded barcode value (string) if found, None otherwise.
    """
    try:
        # Read image using OpenCV
        image = cv2.imread(image_path)
        if image is None:
            logger.error(f"Failed to read image at path: {image_path}")
            return None

        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Try different preprocessing techniques to improve barcode detection
        # 1. Original grayscale
        barcodes = decode(gray)
        if barcodes:
            return barcodes[0].data.decode('utf-8')

        # 2. Thresholding
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        barcodes = decode(thresh)
        if barcodes:
            return barcodes[0].data.decode('utf-8')

        # 3. Gaussian blur + threshold
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh_blur = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        barcodes = decode(thresh_blur)
        if barcodes:
            return barcodes[0].data.decode('utf-8')

        logger.warning(f"No barcode found in image at path: {image_path}")
        return None

    except Exception as e:
        logger.error(f"Error reading barcode from image: {str(e)}")
        return None

@login_required
def calorie_tracker_view(request):
    """Displays the calorie tracker calendar and handles adding/editing entries.

    GET: Renders the calendar for a target month (defaulting to current month),
         showing daily totals and statistics.
    POST: Adds a new calorie entry or updates an existing one based on form data.
    """
    user = request.user
    today = timezone.now().date()

    # --- Determine Target Month ---
    try:
        target_year = int(request.GET.get('year', today.year))
        target_month = int(request.GET.get('month', today.month))
        # Basic validation for month/year range if needed
        target_date = timezone.datetime(target_year, target_month, 1).date()
    except (ValueError, TypeError):
        target_date = today.replace(day=1) # Default to first day of current month on error
        target_year = target_date.year
        target_month = target_date.month

    # --- Handle POST request (Add OR Edit entry) ---
    if request.method == 'POST':
        try:
            entry_id = request.POST.get('entry_id') # Check if we are editing
            entry_date_str = request.POST.get('entry_date')
            entry_calories_str = request.POST.get('entry_calories')
            entry_notes = request.POST.get('entry_notes', '').strip()

            # Basic Validation (applies to both add and edit)
            if not entry_date_str or entry_calories_str is None:
                messages.error(request, "Date and calories are required.")
                # Redirect based on context? For simplicity, redirect to tracker main page
                return redirect('calorie_tracker')

            entry_date = timezone.datetime.strptime(entry_date_str, '%Y-%m-%d').date()
            entry_calories = int(entry_calories_str)

            # Basic validation for calorie value
            if entry_calories < 0:
                 messages.error(request, "Calories cannot be negative.")
                 return redirect('calorie_tracker')

            # --- Edit existing entry ---
            if entry_id:
                try:
                    entry_to_edit = get_object_or_404(CalorieEntry, id=entry_id, user=user)
                    entry_to_edit.date = entry_date
                    entry_to_edit.calories = entry_calories
                    entry_to_edit.notes = entry_notes
                    entry_to_edit.save()
                    messages.success(request, f"Successfully updated entry for {entry_date_str}.")
                    logger.info(f"User {user.username} updated CalorieEntry ID {entry_id}.")
                except Http404:
                     messages.error(request, "Entry not found or you don't have permission to edit it.")
                except Exception as e_edit: # Catch potential errors during update
                     logger.error(f"Error updating CalorieEntry ID {entry_id} for user {user.username}: {e_edit}", exc_info=True)
                     messages.error(request, "An unexpected error occurred while updating the entry.")

            # --- Create new entry ---
            else:
                CalorieEntry.objects.create(
                    user=user,
                    date=entry_date,
                    calories=entry_calories,
                    notes=entry_notes
                )
                messages.success(request, f"Successfully added {entry_calories} kcal for {entry_date_str}.")
                logger.info(f"User {user.username} added new CalorieEntry for date {entry_date_str}.")

        except ValueError:
            messages.error(request, "Invalid number format for calories or date.")
        except Exception as e:
            logger.error(f"Error adding/editing calorie entry for user {user.username}: {e}", exc_info=True)
            messages.error(request, "An unexpected error occurred while processing the entry.")

        # Redirect back to the tracker page (or maybe the specific day?) after POST
        # Redirecting to the main tracker page is simpler for now
        return redirect('calorie_tracker') # Consider redirecting to ?year=...&month=...

    # --- Handle GET request (Display page for TARGET month) ---
    # Get user's goal
    try:
        profile = UserProfile.objects.get(user=user)
        daily_goal = profile.daily_calorie_goal
    except UserProfile.DoesNotExist:
        daily_goal = None

    # Calculate date range for the TARGET month
    first_day_of_target_month = target_date
    next_month_logic = first_day_of_target_month.replace(day=28) + timedelta(days=4)
    last_day_of_target_month = (next_month_logic - timedelta(days=next_month_logic.day))

    # Queryset for entries in the TARGET month (fetch related data needed for editing later)
    entries_this_month = CalorieEntry.objects.filter(
        user=user,
        date__gte=first_day_of_target_month,
        date__lte=last_day_of_target_month
    ).order_by('date', 'id') # Order by date, then ID for consistency within a day

    # Calculate daily totals for the TARGET month, including status vs goal
    daily_totals_qs = entries_this_month.values('date') \
                                        .annotate(total_calories=Sum('calories')) \
                                        .order_by('date')
    # --- MODIFICATION: Include goal comparison in daily totals ---
    daily_totals_dict = {}
    for item in daily_totals_qs:
        date_str = item['date'].strftime('%Y-%m-%d')
        total = item['total_calories']
        status = 'ok' # Default
        if daily_goal is not None:
            if total > daily_goal:
                status = 'over'
            # Add elif for 'under' or 'exact' if needed for more granular coloring
        daily_totals_dict[date_str] = {'total': total, 'status': status}


    # Calculate overall average calories per day for the target month
    avg_target_month_result = entries_this_month.values('date') \
                                                .annotate(daily_total=Sum('calories')) \
                                                .aggregate(average_calories=Avg('daily_total'))
    avg_target_month = round(avg_target_month_result['average_calories']) if avg_target_month_result['average_calories'] else 0

    # Calculate rolling 7-day and 30-day averages ending today
    seven_days_ago = today - timedelta(days=7)
    avg_last_7_days_result = CalorieEntry.objects.filter(
        user=user, date__gte=seven_days_ago, date__lte=today
    ).values('date').annotate(daily_total=Sum('calories')).aggregate(average_calories=Avg('daily_total'))
    avg_last_7_days = round(avg_last_7_days_result['average_calories']) if avg_last_7_days_result['average_calories'] else 0

    thirty_days_ago = today - timedelta(days=30)
    avg_last_30_days_result = CalorieEntry.objects.filter(
        user=user, date__gte=thirty_days_ago, date__lte=today
    ).values('date').annotate(daily_total=Sum('calories')).aggregate(average_calories=Avg('daily_total'))
    avg_last_30_days = round(avg_last_30_days_result['average_calories']) if avg_last_30_days_result['average_calories'] else 0

    # Generate list of years for the year selection dropdown
    current_year = today.year
    year_range = list(range(current_year - 4, current_year + 1))
    year_range.reverse()

    # --- MODIFICATION: Prepare entries data for detailed view (modal/inline) ---
    # Group entries by date for easier access in the template/JS
    entries_by_date = {}
    for entry in entries_this_month:
        date_str = entry.date.strftime('%Y-%m-%d')
        if date_str not in entries_by_date:
            entries_by_date[date_str] = []
        entries_by_date[date_str].append({
            'id': entry.id,
            'calories': entry.calories,
            'notes': entry.notes if entry.notes else '', # Ensure notes is a string
             # Add other fields if needed
        })

    context = {
        'current_date_str': today.strftime('%Y-%m-%d'),
        'target_year': target_year,
        'target_month': target_month,
        'year_options': year_range,
        'daily_totals_json': json.dumps(daily_totals_dict), # Now includes status vs goal
        'avg_target_month': avg_target_month,
        'avg_last_7_days': avg_last_7_days,
        'avg_last_30_days': avg_last_30_days,
        'daily_goal': daily_goal,
        'entries_by_date_json': json.dumps(entries_by_date), # Pass detailed entries grouped by date
    }
    return render(request, 'foodscan/calorie_tracker.html', context)

@login_required
@require_POST # Ensures this view only accepts POST requests
def delete_calorie_entries(request):
    """Handles AJAX requests to delete a specific CalorieEntry.

    Expects 'entry_id' in the POST data.
    Returns a JSON response indicating success or failure, along with the
    updated daily total for the affected date.
    """
    user = request.user
    entry_id = request.POST.get('entry_id') # Moved retrieval up
    try:
        # Validate entry_id presence and format
        if not entry_id:
            return JsonResponse({'status': 'error', 'message': 'Entry ID not provided.'}, status=400)

        # Attempt to convert entry_id to integer *inside* the try block
        try:
            entry_id_int = int(entry_id)
        except ValueError:
            # This except catches the int() conversion error
            return JsonResponse({'status': 'error', 'message': 'Invalid Entry ID format.'}, status=400)

        # Find the entry, ensuring it belongs to the requesting user
        entry_to_delete = get_object_or_404(CalorieEntry, id=entry_id_int, user=user)

        # Store details before deleting for logging/response
        deleted_date = entry_to_delete.date.strftime('%Y-%m-%d')
        deleted_calories = entry_to_delete.calories

        entry_to_delete.delete()

        logger.info(f"User {user.username} deleted CalorieEntry ID {entry_id_int} (Date: {deleted_date}, Calories: {deleted_calories}).")
        # Return remaining total for the day? Optional, but helpful for frontend update.
        # Calculate remaining total for the specific day
        remaining_total = CalorieEntry.objects.filter(user=user, date=deleted_date).aggregate(total=Sum('calories'))['total'] or 0

        return JsonResponse({
            'status': 'success',
            'message': f'Deleted entry ({deleted_calories} kcal) for {deleted_date}.',
            'remaining_total': remaining_total, # Send back the new total for the day
            'date': deleted_date # Send back the date for easy frontend update
            })

    except Http404:
        # This except catches the get_object_or_404 error
        logger.warning(f"User {user.username} attempted to delete non-existent or unauthorized CalorieEntry ID {entry_id}.")
        return JsonResponse({'status': 'error', 'message': 'Entry not found or not authorized.'}, status=404)
    except Exception as e:
        # This is a general catch-all for other unexpected errors
        logger.error(f"Error deleting calorie entry ID {entry_id} for user {user.username}: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': 'An unexpected error occurred.'}, status=500)