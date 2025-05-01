import re
import json
import requests # Added for API calls
import logging # Added for logging errors
from django.conf import settings # Added to get API key
import base64 # Needed for image encoding
import mimetypes # Needed to determine image type

# Setup basic logging
logger = logging.getLogger(__name__)

# --- Internal Helper Function --- 

def _get_numeric_nutrient(nutritional_data, key):
    """Safely retrieves and converts a nutrient value to float from the nutritional data dict.
    
    Args:
        nutritional_data (dict): The dictionary containing nutrient keys and values.
        key (str): The nutrient key to retrieve (e.g., 'sugars_100g').

    Returns:
        float: The numeric value if found and valid, otherwise None.
    """
    if not nutritional_data or not isinstance(nutritional_data, dict):
        return None
        
    val = nutritional_data.get(key)
    if val is None: 
        return None
    try: 
        # Handle potential commas as decimal separators and convert
        val_str = str(val).replace(',', '.')
        return float(val_str)
    except (ValueError, TypeError): 
        logger.warning(f"Could not convert nutritional value for key '{key}' to float: {val}")
        return None

def extract_ingredients(ocr_text):
    """Extracts ingredient list from OCR text using Gemini API with enhanced multilingual deduplication.
    
    Sends OCR text to Gemini asking it to identify ingredients, group multilingual variations,
    and return a structured JSON list of unique ingredients and their variations.
    Returns only the list of primary ingredient names.
    """
    api_key = settings.AI_ANALYZER_API_KEY
    if not api_key or api_key == 'YOUR_AI_SERVICE_API_KEY_HERE':
        logger.error("Gemini API Key not configured for ingredient extraction.")
        return []

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}

    # Define the example format separately to avoid f-string issues
    example_format = '''[
  {
    "primary_name": "Sugar",
    "variations": ["Sugar", "Azúcar (Spanish)", "Açúcar (Portuguese)"]
  },
  {
    "primary_name": "Cocoa Butter",
    "variations": ["Cocoa butter", "Manteca de cacao (Spanish)", "Manteiga de cacau (Portuguese)"]
  }
]'''

    # Prompt instructing the AI to act as an expert label analyst, handle multiple languages,
    # deduplicate, and return a specific JSON format.
    prompt = f"""\
You are an expert in analyzing product labels and extracting ingredients, with deep knowledge of ingredient names across multiple languages. Your task is to identify and extract ingredients from the provided OCR text, consolidating identical ingredients that appear in different languages.

Input OCR Text:
```
{ocr_text}
```

Instructions:
1. Identify the ingredients list section from the text.
2. Extract ONLY actual ingredients, excluding:
   - Nutritional information (e.g., "Energy", "Fat", "Protein")
   - Headers or labels (e.g., "Ingredients:", "Contains:", "May contain:")
   - Percentages and quantities
   - Manufacturing information
   - Any other non-ingredient text
3. For each ingredient:
   - Identify if it appears multiple times in different languages
   - Group identical ingredients together (e.g., "Sugar/Azucar/Acucar" should be one entry)
   - Keep track of all language variations
4. Return a JSON array of objects, where each object represents a unique ingredient with its variations.

Example Output Format:
{example_format}

JSON Output:
"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    })

    try:
        response = requests.post(api_url, headers=headers, data=payload)
        response.raise_for_status()
        response_data = response.json()

        if not response_data.get('candidates'):
            logger.error("No candidates in Gemini API response")
            return []

        ai_response_text = response_data['candidates'][0]['content']['parts'][0]['text']
        
        try:
            # Parse the JSON array response
            ingredients_data = json.loads(ai_response_text)
            if not isinstance(ingredients_data, list):
                logger.error("Gemini response is not a list")
                return []
            
            # Extract just the primary names for the ingredient list
            ingredients_list = [item['primary_name'] for item in ingredients_data if isinstance(item, dict) and 'primary_name' in item]
            
            # Log the variations for debugging/verification
            for item in ingredients_data:
                if isinstance(item, dict) and 'primary_name' in item and 'variations' in item:
                    logger.info(f"Consolidated '{item['primary_name']}' from variations: {item['variations']}")
            
            logger.info(f"Successfully extracted {len(ingredients_list)} unique ingredients (after deduplication) via Gemini")
            return ingredients_list

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response as JSON: {e}")
            return []

    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Gemini API for ingredient extraction: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error during ingredient extraction: {e}")
        return []

def call_gemini_api(ingredient_list):
    """Calls the Gemini API to classify ingredients based on safety, nutrition, and dietary compatibility.
    
    Sends a list of potential ingredients (strings) to Gemini. The prompt asks the AI 
    to evaluate EACH item, determine if it's a food/cosmetic ingredient, classify its 
    health impact (good, bad, caution, neutral), provide dietary flags (vegan, etc.),
    and return a structured JSON list of results.
    Emphasizes consistent classification and nuanced handling of GRAS additives.
    """
    api_key = settings.AI_ANALYZER_API_KEY
    if not api_key or api_key == 'YOUR_AI_SERVICE_API_KEY_HERE':
        logger.error("Gemini API Key not configured in settings.py")
        return None

    # Use the Flash model for faster responses, suitable for this task
    # NOTE: Ensure the model name is correct for your enabled APIs
    # Using v1beta as per user example, adjust if needed
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

    headers = {'Content-Type': 'application/json'}

    # Prompt defining the analyst persona, input format, detailed classification rules (good, bad, caution, neutral),
    # required output keys (name, english_name, status, dietary flags), and the desired JSON list format.
    # Includes specific instructions on handling GRAS ingredients.
    prompt = f"""\
You are an ingredient safety, nutrition, and dietary compatibility analyst evaluating ingredients from product labels (food and cosmetics). Your goal is to classify EACH item extracted from OCR text, provide an English translation, indicate its potential impact, determine dietary compatibility, AND assign a health-impact status with nuance, considering the context of processed foods.

Input Items: A JSON list of strings potentially containing ingredients.
{json.dumps(ingredient_list)}

Instructions:
Process EACH item from the input list and return a JSON list of objects. For each item:
1.  Identify if it's an ingredient found in food or cosmetics.
2.  If NOT an ingredient, create object: {{"name": "<original item>", "status": "not_ingredient"}}
3.  If it IS an ingredient, create an object with the following keys:
    *   `name`: Original item string.
    *   `english_name`: Common English name. Handle codes.
    *   `status`: Classify STRICTLY using ONE of the following:
        *   `good`: RESERVED STRICTLY for minimally processed, nutrient-dense whole foods (e.g., Whole Fruits/Vegetables/Grains, Nuts, Seeds as primary ingredients).
        *   `bad`: Ingredients with significant, widely discussed health concerns, restrictions, or known negative impacts (e.g., Trans Fats/Partially Hydrogenated Oils, HFCS, Aspartame, BHA/BHT).
        *   `caution`: Ingredients requiring awareness. Use for: **Additives WITH known potential issues for some people** (e.g., Xanthan Gum - digestive issues, Sulfites, MSG), **Processed Sweeteners** (sugar, syrups), **Refined Oils/Fats commonly overused or controversial** (e.g., Palm Oil), **Salt**, ingredients with **significant controversy**, or components indicating high processing levels.
        *   `neutral`: Ingredients generally considered benign/inert with minimal direct impact OR **common additives Generally Recognized As Safe (GRAS) WITHOUT specific known issues mentioned for them** (e.g., Potassium Sorbate, Calcium Propionate, Sodium Benzoate). Also use for: Water, basic starches, basic refined flours, basic fats/oils used moderately (Butter, Sunflower Oil, Cocoa Butter), leavening agents, basic acids/antioxidants (Citric Acid, Ascorbic Acid). This category signifies lower concern compared to 'caution'.
    *   `is_vegetarian`, `is_vegan`, `is_gluten_free`: Boolean (true/false).
    *   `description`: (Optional) Brief, neutral English function.
    *   `impact`: (Optional) Short, neutral English considerations (e.g., "Common preservative, generally safe", "Thickener, may cause digestive issues for some").

CONSISTENCY IS CRITICAL: Apply the SAME status/flags consistently. **Crucially, if an additive is described as GRAS or generally safe and lacks specific warnings (like digestive issues, allergen concerns etc.), classify it as `neutral`, NOT `caution`. Reserve `caution` for additives with explicit potential concerns or those contributing heavily to unhealthy profiles (like primary sweeteners/fats/salt).**

Output Format: ONLY the valid JSON list of result objects.

JSON Output:
"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2 # Lower temperature further for consistency
        }
    })

    try:
        response = requests.post(api_url, headers=headers, data=payload, timeout=30)
        response.raise_for_status()
        
        response_data = response.json()
        
        # Defensive checking of response structure
        if not response_data.get('candidates') or not isinstance(response_data['candidates'], list) or len(response_data['candidates']) == 0:
            logger.error(f"Gemini response missing 'candidates'. Full Response: {response_data}")
            return None
        
        content = response_data['candidates'][0].get('content')
        if not content or not content.get('parts') or not isinstance(content['parts'], list) or len(content['parts']) == 0:
            logger.error(f"Gemini response missing 'content' or 'parts'. Full Response: {response_data}")
            return None
            
        ai_response_text = content['parts'][0].get('text')
        if not ai_response_text:
            logger.error(f"Gemini response missing 'text' in parts. Full Response: {response_data}")
            return None
            
        # Attempt to parse the JSON string returned by the AI
        try:
            analysis_data_raw = json.loads(ai_response_text)
            analysis_list = None

            # Flexible parsing: Check for direct list, dict with 'results', or dict with 'items'
            if isinstance(analysis_data_raw, list):
                analysis_list = analysis_data_raw
                logger.info("AI response was a direct list.")
            elif isinstance(analysis_data_raw, dict):
                if 'results' in analysis_data_raw and isinstance(analysis_data_raw['results'], list):
                    analysis_list = analysis_data_raw['results']
                    logger.info("AI response wrapped in 'results' key. Extracted list.")
                elif 'items' in analysis_data_raw and isinstance(analysis_data_raw['items'], list):
                    analysis_list = analysis_data_raw['items']
                    logger.info("AI response wrapped in 'items' key. Extracted list.")
                else:
                     logger.error(f"Gemini response is a dict but missing expected list key ('results' or 'items'): {analysis_data_raw}")
                     return None
            else:
                logger.error(f"Gemini response text is not a list or a known dictionary structure: {ai_response_text}")
                return None
                
            # === Validation on the extracted analysis_list ===
            if analysis_list is None:
                 logger.error("Internal error: analysis_list is None after parsing checks.")
                 return None

            valid_items = []
            allowed_statuses = {'good', 'bad', 'neutral', 'caution', 'not_ingredient'}
            original_input_set = set(ingredient_list) 
            
            for item in analysis_list:
                original_name_from_ai = item.get('name') 
                if not original_name_from_ai or not isinstance(item, dict):
                    logger.warning(f"Gemini response item invalid structure or missing name: {item}")
                    continue 
                
                if original_name_from_ai not in original_input_set:
                    logger.warning(f"Gemini returned analysis for an item not in the original input list: {item}")
                    continue
                    
                status = item.get('status')
                if status == 'not_ingredient':
                    valid_items.append({'name': original_name_from_ai, 'status': 'not_ingredient'})
                elif status in allowed_statuses:
                     # Ensure essential keys are present, including new dietary flags
                     required_keys = ['english_name', 'is_vegetarian', 'is_vegan', 'is_gluten_free'] 
                     if all(key in item for key in required_keys):
                         # Validate boolean types for dietary flags
                         if not all(isinstance(item.get(key), bool) for key in ['is_vegetarian', 'is_vegan', 'is_gluten_free']):
                             logger.warning(f"Gemini response item has non-boolean dietary flags for ingredient '{original_name_from_ai}': {item}")
                             continue # Skip if dietary flags are not booleans
                             
                         # Add optional fields if they exist
                         valid_item_data = {
                             'name': original_name_from_ai,
                             'english_name': item['english_name'],
                             'status': status,
                             'is_vegetarian': item['is_vegetarian'],
                             'is_vegan': item['is_vegan'],
                             'is_gluten_free': item['is_gluten_free']
                         }
                         if 'description' in item:
                             valid_item_data['description'] = item['description']
                         if 'impact' in item:
                             valid_item_data['impact'] = item['impact']
                         valid_items.append(valid_item_data)
                     else:
                          logger.warning(f"Gemini response item missing required keys (incl. dietary flags) for ingredient '{original_name_from_ai}': {item}")
                else:
                    logger.warning(f"Gemini response item invalid status '{status}' for ingredient '{original_name_from_ai}': {item}")
            
            return valid_items

        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to decode Gemini JSON response: {json_err}\nResponse text: {ai_response_text}")
            return None
        except KeyError as key_err:
             logger.error(f"KeyError accessing Gemini response structure: {key_err}\nFull Response: {response_data}")
             return None


    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Gemini API: {e}")
        return None
    except Exception as e:
        # Catch unexpected errors during processing
        logger.error(f"Unexpected error processing Gemini response: {e}")
        return None

def call_gemini_for_nutrition(ocr_text):
    """Calls Gemini API to extract structured nutritional data (per 100g) from OCR text.
    
    Handles potentially long OCR text by attempting to find a relevant subset.
    Sends the text to Gemini asking for specific nutrient values (kcal, kj, fat, etc.) 
    in a defined JSON format.
    Validates the response and converts values to numeric types.
    Calculates sodium from salt if necessary.
    """
    api_key = settings.AI_ANALYZER_API_KEY
    if not api_key or api_key == 'YOUR_AI_SERVICE_API_KEY_HERE':
        logger.error("Gemini API Key not configured for nutrition extraction.")
        return None

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}

    # Limit OCR text length to avoid overly long prompts (e.g., last 1000 chars? Or find relevant section?)
    # For simplicity now, let's just pass a reasonable chunk if it's very long.
    max_ocr_length = 1500
    if len(ocr_text) > max_ocr_length:
        # Try to find a potential start of nutrition info
        nutrition_keywords = ["nutritională", "nutrition", "odżywcza", "valoare energetică", "wartość energetyczna"]
        start_index = -1
        for keyword in nutrition_keywords:
            idx = ocr_text.lower().rfind(keyword.lower(), 0, len(ocr_text) - max_ocr_length // 2) # Search in first half
            if idx != -1:
                start_index = max(0, idx - 100) # Give some context before
                break
        if start_index != -1:
             ocr_text_subset = ocr_text[start_index : start_index + max_ocr_length]
             logger.info(f"Using subset of long OCR text for nutrition analysis (starting near '{keyword}').")
        else:
             ocr_text_subset = ocr_text[-max_ocr_length:] # Fallback to last chars
             logger.info(f"Using last {max_ocr_length} chars of long OCR text for nutrition analysis.")
    else:
        ocr_text_subset = ocr_text

    # Prompt asking the AI to extract specific nutritional keys per 100g/ml,
    # handle OCR errors, and return only a valid JSON object.
    prompt = f"""\
You are a data extraction assistant. Analyze the following OCR text from a product label, which might contain errors and mixed languages (like Romanian, Polish, English). Your goal is to extract the nutritional information **per 100g** (or per 100ml if specified, but assume 100g otherwise).

Input OCR Text:
```
{ocr_text_subset}
```

Instructions:
1. Identify the section containing nutritional values per 100g.
2. Extract the numerical values for the following nutrients if available. Handle potential OCR errors (e.g., 'O' instead of '0', commas vs periods as decimals).
3. Return the data as a JSON object using these EXACT keys:
    * `energy-kcal_100g` (Kilocalories)
    * `energy-kj_100g` (Kilojoules)
    * `fat_100g` (Total Fat in grams)
    * `saturated-fat_100g` (Saturated Fat in grams - often labeled 'din care acizi grași saturați', 'w tym kwasy tłuszczowe nasycone', 'of which saturates')
    * `carbohydrates_100g` (Total Carbohydrates in grams)
    * `sugars_100g` (Sugars in grams - often labeled 'din care zaharuri', 'w tym cukry', 'of which sugars')
    * `fiber_100g` (Fiber in grams)
    * `proteins_100g` (Proteins in grams)
    * `salt_100g` (Salt in grams)
    * `sodium_100g` (Sodium in grams - calculate if only salt is given: sodium = salt / 2.5)

4. If a nutrient is not found or its value cannot be reliably extracted, omit its key from the JSON.
5. Provide ONLY the valid JSON object as output.

JSON Output:
"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1 # Low temperature for factual extraction
        }
    })

    try:
        response = requests.post(api_url, headers=headers, data=payload, timeout=25) # Slightly lower timeout
        response.raise_for_status()
        response_data = response.json()

        # Simplified validation structure from call_gemini_api
        if not response_data.get('candidates') or not response_data['candidates'][0].get('content') or not response_data['candidates'][0]['content'].get('parts'):
            logger.error(f"Gemini Nutrition response invalid structure: {response_data}")
            return None
        
        ai_response_text = response_data['candidates'][0]['content']['parts'][0].get('text')
        if not ai_response_text:
            logger.error(f"Gemini Nutrition response missing text: {response_data}")
            return None
            
        try:
            nutrition_dict = json.loads(ai_response_text)
            if not isinstance(nutrition_dict, dict):
                 logger.error(f"Gemini Nutrition response is not a JSON dictionary: {ai_response_text}")
                 return None
            
            # Validate the extracted keys and numeric values.
            valid_data = {}
            allowed_keys = {
                'energy-kcal_100g', 'energy-kj_100g', 'fat_100g', 'saturated-fat_100g',
                'carbohydrates_100g', 'sugars_100g', 'fiber_100g', 'proteins_100g',
                'salt_100g', 'sodium_100g'
            }
            for key, value in nutrition_dict.items():
                if key in allowed_keys:
                    if value is None:
                        valid_data[key] = None
                    else:
                        try:
                            # Convert to float to ensure it's numeric, store as float
                            float_val = float(str(value).replace(',', '.')) 
                            # Add sanity check for obviously wrong values (e.g., > 100g per 100g)
                            if key not in ['energy-kcal_100g', 'energy-kj_100g'] and float_val > 100.0:
                                logger.warning(f"Gemini returned potentially unrealistic value for {key}: {float_val}. Skipping.")
                                continue
                            elif key == 'salt_100g' and float_val > 50.0:
                                logger.warning(f"Gemini returned potentially unrealistic value for {key}: {float_val}. Skipping.")
                                continue
                                
                            valid_data[key] = float_val
                        except (ValueError, TypeError):
                             logger.warning(f"Gemini returned non-numeric value for {key}: {value}. Skipping.")
                else:
                    logger.warning(f"Gemini returned unexpected key: {key}")

            # Calculate sodium from salt if salt exists and sodium doesn't
            if 'salt_100g' in valid_data and 'sodium_100g' not in valid_data and valid_data['salt_100g'] is not None:
                valid_data['sodium_100g'] = round(valid_data['salt_100g'] / 2.5, 3)
                logger.info(f"Calculated sodium_100g based on salt value.")
                
            logger.info(f"Successfully extracted nutritional data via Gemini: {valid_data}")
            return valid_data

        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to decode Gemini Nutrition JSON response: {json_err}\nResponse text: {ai_response_text}")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Gemini API for nutrition: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Gemini Nutrition response: {e}")
        return None

# --- NEW Function to perform OCR using Gemini Vision ---
def get_text_from_image_gemini(image_path):
    """Extracts text from an image file using Gemini Vision API.

    Reads image, encodes to base64, determines MIME type, and sends to Gemini
    asking for text extraction.
    """
    api_key = settings.AI_ANALYZER_API_KEY
    if not api_key or api_key == 'YOUR_AI_SERVICE_API_KEY_HERE':
        logger.error("Gemini API Key not configured for vision OCR.")
        return None

    # Determine MIME type from file extension. Consider using python-magic for more robustness.
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or not mime_type.startswith('image/'):
        logger.error(f"Could not determine valid image MIME type for: {image_path}")
        # Fallback or default?
        mime_type = 'image/jpeg' # Defaulting to JPEG, might fail for other types
        logger.warning(f"Defaulting MIME type to {mime_type}")

    # Read image file and encode it in base64
    try:
        with open(image_path, "rb") as image_file:
            image_data = base64.b64encode(image_file.read()).decode('utf-8')
    except FileNotFoundError:
        logger.error(f"Image file not found at path: {image_path}")
        return None
    except Exception as e:
        logger.error(f"Error reading or encoding image file {image_path}: {e}")
        return None

    # Using gemini-1.5-flash which supports vision
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}

    # Simple prompt asking for text extraction
    prompt_text = "Extract all text content from this image of a product label. Preserve line breaks where possible."

    # Construct the multimodal payload
    payload = json.dumps({
        "contents": [
            {
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_data
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            # No specific generation config needed for simple text extraction usually
            # "temperature": 0.1 # Can keep low if needed
        }
    })

    try:
        response = requests.post(api_url, headers=headers, data=payload, timeout=45) # Increased timeout for image processing
        response.raise_for_status()
        response_data = response.json()

        # Validation similar to other Gemini calls
        if not response_data.get('candidates') or not response_data['candidates'][0].get('content') or not response_data['candidates'][0]['content'].get('parts'):
            logger.error(f"Gemini Vision OCR response invalid structure: {response_data}")
            return None
        
        # Assuming the text is in the first part of the response
        extracted_text = response_data['candidates'][0]['content']['parts'][0].get('text')
        
        if extracted_text:
            logger.info(f"Successfully extracted text via Gemini Vision OCR (length: {len(extracted_text)}).")
            # logger.debug(f"Gemini OCR Text: {extracted_text[:500]}...") # Log beginning of text
            return extracted_text
        else:
            logger.warning(f"Gemini Vision OCR returned no text. Response: {response_data}")
            return "" # Return empty string instead of None if no text found but API call succeeded

    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Gemini API for Vision OCR: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Gemini Vision OCR response: {e}")
        return None

def analyze_ingredients(ocr_text, user_allergens=None, user_dietary_prefs=None, nutritional_data=None):
    """Main orchestration function for analyzing ingredients from OCR text.

    Steps:
    1. Extract potential ingredients from OCR using Gemini (`extract_ingredients`).
    2. Determine final nutritional data: Use provided `nutritional_data` (from OFF) if valid, 
       otherwise attempt extraction from OCR using Gemini (`call_gemini_for_nutrition`).
    3. Classify extracted ingredients using Gemini (`call_gemini_api`).
    4. Calculate health score based on classifications and nutritional data (`calculate_score`).
    5. Generate warnings based on classifications, user preferences, and nutrition (`generate_warnings`).
    6. Return a dictionary containing all analysis results.
    """
    
    # --- Step 1: Extract ingredients from OCR ---
    logger.info("Step 1: Extracting potential ingredients from OCR text...")
    extracted_items = extract_ingredients(ocr_text)
    if not extracted_items:
        logger.warning("No potential ingredients extracted from OCR text.")
        return {
            'text': ocr_text,
            'ingredients': [],
            'score': 50,
            'warnings': [{'type': 'extraction_failed', 'message': 'Could not extract potential ingredients list reliably.', 'ingredients': []}],
            'nutritional_data': {} # Return empty dict for nutritional data
        }

    # --- Step 2: Prepare Nutritional Data (Prioritize OFF, fallback to Gemini on OCR/Text) ---
    logger.info("Step 2: Preparing nutritional data...")
    final_nutritional_data = {}
    # Check if nutritional_data from argument (OFF) is valid
    if nutritional_data and isinstance(nutritional_data, dict) and nutritional_data:
        final_nutritional_data.update(nutritional_data) 
        logger.info(f"Using nutritional data provided (likely from OFF): {final_nutritional_data}")
    else:
        # If no valid data from OFF, try extracting from OCR using Gemini
        logger.info("No valid nutritional data provided, attempting extraction from OCR via Gemini.")
        nutritional_data_from_gemini = call_gemini_for_nutrition(ocr_text)
        if nutritional_data_from_gemini and isinstance(nutritional_data_from_gemini, dict):
            final_nutritional_data.update(nutritional_data_from_gemini)
            logger.info(f"Using nutritional data extracted from OCR via Gemini: {final_nutritional_data}")
        else:
            logger.warning("Failed to extract nutritional data from OCR via Gemini.")
            # final_nutritional_data remains empty {}

    # --- Step 3: Call AI for ingredient classification ---
    logger.info(f"Step 3: Classifying {len(extracted_items)} extracted items via Gemini...")
    ai_analysis_results = call_gemini_api(extracted_items)
    classified_ingredients = []

    if ai_analysis_results:
        for analysis_data in ai_analysis_results:
            status = analysis_data.get('status')
            original_name = analysis_data.get('name')
            if status == 'not_ingredient':
                logger.info(f"AI classified '{original_name}' as not an ingredient. Skipping.")
                continue
            english_name = analysis_data.get('english_name', original_name)
            if status not in ['good', 'bad', 'neutral', 'caution']:
                logger.warning(f"Received unexpected status '{status}' for {original_name}, defaulting to neutral.")
                status = 'neutral'
            # --- Ensure dietary flags are included in the 'info' dict --- 
            classified_ingredients.append({
                'name': original_name,
                'status': status,
                'info': {
                    'name': original_name,
                    'english_name': english_name,
                    'description': analysis_data.get('description', 'N/A'),
                    'impact': analysis_data.get('impact', 'N/A'),
                    'is_vegetarian': analysis_data.get('is_vegetarian'), # Default to None if missing
                    'is_vegan': analysis_data.get('is_vegan'),             # Default to None if missing
                    'is_gluten_free': analysis_data.get('is_gluten_free') # Default to None if missing
                }
            })
        logger.info("Successfully processed AI analysis results and filtered non-ingredients.")
    else:
        logger.error("Gemini API call for ingredients failed or returned invalid data. Cannot classify ingredients.")
        classified_ingredients = []

    # --- Step 4: Calculate Score and Generate Warnings using FINAL nutritional data ---
    logger.info("Step 4: Calculating score and generating warnings...")
    score = calculate_score(classified_ingredients, final_nutritional_data)
    warnings = generate_warnings(classified_ingredients, ocr_text, user_allergens, user_dietary_prefs, final_nutritional_data)

    # --- Step 5: Return results including the final nutritional data ---
    logger.info("Step 5: Finalizing analysis result package.")
    return {
        'text': ocr_text,
        'ingredients': classified_ingredients,
        'score': score,
        'warnings': warnings,
        'nutritional_data': final_nutritional_data 
    }

def calculate_score(classified_ingredients, nutritional_data=None):
    """Calculate a health score (0-100) based on ingredient classifications and nutritional data.
    
    Score starts at a base value and is adjusted based on:
    - Ingredient Status: Points added for 'good', subtracted for 'bad'/'caution'.
    - Nutritional Data (if available): Penalties for high saturated fat, sugars, salt. 
                                     Bonuses for high fiber, protein.
                                     
    NOTE: Hardcoded points/thresholds should ideally be moved to settings for configurability.
    """

    # --- Part 1: Score based on Ingredients --- 
    # Use scoring parameters from settings.py
    ingredient_score = settings.FS_SCORE_BASE

    if classified_ingredients: 
        for ingredient in classified_ingredients:
            status = ingredient.get('status')
            if status == 'good':
                ingredient_score += settings.FS_SCORE_POINTS_GOOD
            elif status == 'bad':
                ingredient_score += settings.FS_SCORE_POINTS_BAD
            elif status == 'caution':
                ingredient_score += settings.FS_SCORE_POINTS_CAUTION
            elif status == 'neutral':
                ingredient_score += settings.FS_SCORE_POINTS_NEUTRAL
    else:
        ingredient_score = 50.0 # Assign a moderate default score if no ingredients were classified

    # Ensure score stays within 0-100 bounds after ingredient adjustments
    ingredient_score = max(0.0, min(100.0, ingredient_score))

    # --- Part 2: Adjust score based on Nutrition (if available) --- 
    nutrition_adjustment = 0.0
    if nutritional_data and isinstance(nutritional_data, dict):
        logger.info(f"Applying nutritional score adjustments based on: {nutritional_data}")

        # Use the shared helper function
        saturated_fat = _get_numeric_nutrient(nutritional_data, 'saturated-fat_100g')
        sugars = _get_numeric_nutrient(nutritional_data, 'sugars_100g')
        salt = _get_numeric_nutrient(nutritional_data, 'salt_100g')
        fiber = _get_numeric_nutrient(nutritional_data, 'fiber_100g')
        proteins = _get_numeric_nutrient(nutritional_data, 'proteins_100g')

        # Apply nutritional penalties/bonuses based on thresholds from settings.py
        nutri_config = settings.FS_SCORE_NUTRITION
        
        # Saturated Fat Penalties
        if saturated_fat is not None:
            for i, threshold in enumerate(nutri_config['SAT_FAT_PENALTY_THRESHOLDS']):
                if saturated_fat > threshold:
                    adjustment = nutri_config['SAT_FAT_PENALTY_POINTS'][i]
                    nutrition_adjustment += adjustment
                    logger.debug(f"Penalty: {adjustment} SatFat ({saturated_fat}g > {threshold}g)")
                    break # Apply only the highest penalty

        # Sugars Penalties
        if sugars is not None:
            for i, threshold in enumerate(nutri_config['SUGARS_PENALTY_THRESHOLDS']):
                if sugars > threshold:
                    adjustment = nutri_config['SUGARS_PENALTY_POINTS'][i]
                    nutrition_adjustment += adjustment
                    logger.debug(f"Penalty: {adjustment} Sugars ({sugars}g > {threshold}g)")
                    break

        # Salt Penalties
        if salt is not None:
            for i, threshold in enumerate(nutri_config['SALT_PENALTY_THRESHOLDS']):
                if salt > threshold:
                    adjustment = nutri_config['SALT_PENALTY_POINTS'][i]
                    nutrition_adjustment += adjustment
                    logger.debug(f"Penalty: {adjustment} Salt ({salt}g > {threshold}g)")
                    break

        # Fiber Bonuses
        if fiber is not None:
            for i, threshold in enumerate(nutri_config['FIBER_BONUS_THRESHOLDS']):
                 if fiber > threshold:
                    adjustment = nutri_config['FIBER_BONUS_POINTS'][i]
                    nutrition_adjustment += adjustment
                    logger.debug(f"Bonus: +{adjustment} Fiber ({fiber}g > {threshold}g)")
                    break # Apply only the highest bonus

        # Protein Bonuses
        if proteins is not None:
            for i, threshold in enumerate(nutri_config['PROTEIN_BONUS_THRESHOLDS']):
                 if proteins > threshold:
                    adjustment = nutri_config['PROTEIN_BONUS_POINTS'][i]
                    nutrition_adjustment += adjustment
                    logger.debug(f"Bonus: +{adjustment} Protein ({proteins}g > {threshold}g)")
                    break

        logger.info(f"Nutritional adjustment calculated: {nutrition_adjustment}")
    else:
         logger.info("No nutritional data provided or not a dict, skipping nutritional score adjustment.")

    # --- Part 3: Combine Scores --- 
    final_score = ingredient_score + nutrition_adjustment
    # Ensure final score is clamped between 0 and 100
    final_score = max(0.0, min(100.0, final_score))

    logger.info(f"Ingredient Score Base: {settings.FS_SCORE_BASE:.1f}, Ingredient Adjustment: {ingredient_score - settings.FS_SCORE_BASE:.1f}, Nutrition Adjustment: {nutrition_adjustment:.1f}, Final Score: {final_score:.1f} (Rounded: {round(final_score)}) ")
    return round(final_score)

def generate_warnings(classified_ingredients, ocr_text, user_allergens=None, user_dietary_prefs=None, nutritional_data=None):
    """Generate a list of warnings based on ingredient analysis, user preferences, and nutritional data.
    
    Checks for:
    - Matches against user-specified allergens.
    - Conflicts with user dietary preferences (vegan, vegetarian, gluten-free) based on AI flags.
    - High levels of saturated fat, sugars, or salt based on nutritional data.
    
    NOTE: Hardcoded nutritional thresholds should ideally be moved to settings.
    NOTE: Contains duplicated helper function `get_numeric_nutrient_warn`.
    """
    warnings = []
    # Store the actual user allergen terms that were matched
    matched_user_allergen_terms = set() # Use a set to avoid duplicates

    standard_allergens_lower = []
    custom_allergens_lower = []
    user_allergen_map = {} # Map lowercase term to original user input term

    if user_allergens and isinstance(user_allergens, dict):
        # Store original terms alongside lowercase for display later
        for std_allergen in user_allergens.get('standard', []):
             std_lower = std_allergen.lower()
             standard_allergens_lower.append(std_lower)
             user_allergen_map[std_lower] = std_allergen # Map lower to original

        for cust_allergen in user_allergens.get('custom', '').split(','):
            cust_allergen_stripped = cust_allergen.strip()
            if cust_allergen_stripped:
                 cust_lower = cust_allergen_stripped.lower()
                 custom_allergens_lower.append(cust_lower)
                 user_allergen_map[cust_lower] = cust_allergen_stripped # Map lower to original

    # Check ingredients against user allergens
    if standard_allergens_lower or custom_allergens_lower:
        for ingredient in classified_ingredients:
            # Check against english name primarily, fallback to original name if needed
            # Consider using regex with word boundaries (\b) for more precision if needed
            name_to_check_lower = ingredient['info'].get('english_name', ingredient['name']).lower()

            # Combine standard and custom for simpler checking
            all_user_allergens_lower = standard_allergens_lower + custom_allergens_lower

            for allergen_lower in all_user_allergens_lower:
                # Use word boundary regex for more precise matching? Might be too strict.
                # Simple 'in' check for now.
                if allergen_lower in name_to_check_lower:
                    # Found a match, add the original user-entered term to the set
                    matched_user_allergen_terms.add(user_allergen_map[allergen_lower])
                    # Optimization: Stop checking other allergens against this specific ingredient
                    break 

    # Add a single, consolidated warning listing all matched user allergen terms
    if matched_user_allergen_terms:
        formatted_allergens = ", ".join(sorted(list(matched_user_allergen_terms)))
        warnings.append({
            'type': 'user_allergen_match',
            'short_title': 'User Allergen Match',
            'message': f"Potential user allergen detected! Product contains ingredients related to your specified allergens: {formatted_allergens}.",
            'ingredients': list(matched_user_allergen_terms) 
        })

    # --- Add Warnings based on Dietary Preferences --- 
    if user_dietary_prefs and isinstance(user_dietary_prefs, dict):
        logger.debug(f"Checking dietary preferences for warnings: {user_dietary_prefs}")
        
        # Helper to safely get numeric nutrient value
        # TODO: Refactor this - duplicated from calculate_score
        def get_numeric_nutrient_warn(key):
            val = nutritional_data.get(key)
            if val is None: return None
            try: val_str = str(val).replace(',', '.'); return float(val_str)
            except (ValueError, TypeError): return None
            
        # --- Check for Conflicts with each preference --- 
        
        # Vegetarian Check
        if user_dietary_prefs.get('vegetarian') is True:
            non_vegetarian_ingredients = []
            for ingredient in classified_ingredients:
                # Check if AI classified as NOT vegetarian (and classification is available)
                if ingredient['info'].get('is_vegetarian') is False:
                    non_vegetarian_ingredients.append(ingredient['info'].get('english_name', ingredient['name']))
            if non_vegetarian_ingredients:
                 warnings.append({
                    'type': 'dietary_preference_conflict',
                    'short_title': 'Vegetarian Preference Conflict',
                    'message': f"Dietary Preference Conflict: Product may not be suitable for a Vegetarian diet. Contains potentially non-vegetarian ingredients identified by AI: {', '.join(non_vegetarian_ingredients)}.",
                    'ingredients': non_vegetarian_ingredients
                 })
                 logger.info(f"Added dietary conflict warning for Vegetarian preference: {non_vegetarian_ingredients}")

        # Vegan Check
        if user_dietary_prefs.get('vegan') is True:
            non_vegan_ingredients = []
            for ingredient in classified_ingredients:
                # Check if AI classified as NOT vegan (and classification is available)
                if ingredient['info'].get('is_vegan') is False:
                    non_vegan_ingredients.append(ingredient['info'].get('english_name', ingredient['name']))
            if non_vegan_ingredients:
                 warnings.append({
                    'type': 'dietary_preference_conflict',
                    'short_title': 'Vegan Preference Conflict',
                    'message': f"Dietary Preference Conflict: Product may not be suitable for a Vegan diet. Contains potentially non-vegan ingredients identified by AI: {', '.join(non_vegan_ingredients)}.",
                    'ingredients': non_vegan_ingredients
                 })
                 logger.info(f"Added dietary conflict warning for Vegan preference: {non_vegan_ingredients}")

        # Gluten Free Check
        if user_dietary_prefs.get('gluten_free') is True:
            gluten_containing_ingredients = []
            for ingredient in classified_ingredients:
                 # Check if AI classified as NOT gluten-free (and classification is available)
                if ingredient['info'].get('is_gluten_free') is False:
                    gluten_containing_ingredients.append(ingredient['info'].get('english_name', ingredient['name']))
            if gluten_containing_ingredients:
                 warnings.append({
                    'type': 'dietary_preference_conflict',
                    'short_title': 'Gluten Free Preference Conflict',
                    'message': f"Dietary Preference Conflict: Product may not be suitable for a Gluten Free diet. Contains potentially gluten-containing ingredients identified by AI: {', '.join(gluten_containing_ingredients)}.",
                    'ingredients': gluten_containing_ingredients
                 })
                 logger.info(f"Added dietary conflict warning for Gluten Free preference: {gluten_containing_ingredients}")
        
        # Check for Sugar Free preference conflict (Keep existing logic)
        if user_dietary_prefs.get('sugar_free') is True: # Check if the preference is explicitly True
            contains_sugar_ingredient = False
            sugar_keywords = ['sugar', 'syrup', 'fructose', 'sucrose', 'glucose', 'dextrose', 'zahăr', 'sirop', 'azucar', 'açúcar', 'zaharuri'] # Expanded list
            for ingredient in classified_ingredients:
                name_lower = ingredient.get('name', '').lower()
                english_name_lower = ingredient['info'].get('english_name', '').lower()
                # Check both original and English names for sugar keywords
                if any(keyword in name_lower for keyword in sugar_keywords) or \
                   any(keyword in english_name_lower for keyword in sugar_keywords):
                    contains_sugar_ingredient = True
                    logger.debug(f"Dietary Conflict (Sugar Free): Found sugar-related ingredient '{ingredient.get('name')}'")
                    break # Found one, no need to check further ingredients
            
            # Check nutritional data for sugar content
            sugars_value = get_numeric_nutrient_warn('sugars_100g')
            sugar_free_threshold = 0.5 # g per 100g (typical definition)
            is_high_sugar_nutritionally = sugars_value is not None and sugars_value > sugar_free_threshold
            
            if contains_sugar_ingredient or is_high_sugar_nutritionally:
                message = "Dietary Preference Conflict: Product may not be suitable for a Sugar Free diet."
                details = []
                if contains_sugar_ingredient:
                    details.append("Contains ingredients commonly identified as sugars.")
                if is_high_sugar_nutritionally:
                    details.append(f"Sugar content ({sugars_value:.1f}g per 100g) exceeds the typical threshold for sugar-free products ({sugar_free_threshold}g)." )
                
                warnings.append({
                    'type': 'dietary_preference_conflict', # New type for styling
                    'short_title': 'Sugar Free Preference Conflict',
                    'message': f"{message} { ' '.join(details) }",
                    'ingredients': [] # Not directly linked to specific ingredients here
                })
                logger.info(f"Added dietary conflict warning for Sugar Free preference.")
                
    else:
        logger.debug("Skipping dietary preference warnings: No data or not a dict.")

    # --- Add Warnings based on Nutritional Data (High Sugar, Fat, Salt - keep these separate) --- 
    if nutritional_data and isinstance(nutritional_data, dict):
        logger.debug(f"Checking nutritional data for general warnings: {nutritional_data}")
        
        # Use the shared helper function
        sugars = _get_numeric_nutrient(nutritional_data, 'sugars_100g')
        saturated_fat = _get_numeric_nutrient(nutritional_data, 'saturated-fat_100g')
        salt = _get_numeric_nutrient(nutritional_data, 'salt_100g')

        # Use nutritional warning thresholds from settings.py
        WARN_THRESHOLD_SAT_FAT = settings.FS_WARN_THRESHOLD_SAT_FAT
        WARN_THRESHOLD_SUGARS = settings.FS_WARN_THRESHOLD_SUGARS
        WARN_THRESHOLD_SALT = settings.FS_WARN_THRESHOLD_SALT
        
        # Check Saturated Fat
        if saturated_fat is not None and saturated_fat > WARN_THRESHOLD_SAT_FAT:
            warnings.append({
                'type': 'high_saturated_fat',
                'short_title': 'High Saturated Fat',
                'message': f"High saturated fat content ({saturated_fat:.1f}g per 100g). Excessive intake can impact cardiovascular health.",
                'ingredients': []
            })
            logger.debug(f"Added high saturated fat warning ({saturated_fat}g > {WARN_THRESHOLD_SAT_FAT}g)")
            
        # Check Sugars
        if sugars is not None and sugars > WARN_THRESHOLD_SUGARS:
            warnings.append({
                'type': 'high_sugar',
                'short_title': 'High Sugar',
                'message': f"High sugar content ({sugars:.1f}g per 100g). Consider limiting consumption.",
                'ingredients': []
            })
            logger.debug(f"Added high sugar warning ({sugars}g > {WARN_THRESHOLD_SUGARS}g)")
            
        # Check Salt
        if salt is not None and salt > WARN_THRESHOLD_SALT:
            warnings.append({
                'type': 'high_salt',
                'short_title': 'High Salt',
                'message': f"High salt content ({salt:.2f}g per 100g). Monitor intake, as high salt consumption is linked to high blood pressure.",
                'ingredients': []
            })
            logger.debug(f"Added high salt warning ({salt}g > {WARN_THRESHOLD_SALT}g)")
            
    else:
        logger.debug("Skipping nutritional warnings: No data or not a dict.")

    # --- Existing warnings (Harmful, Caution, E-numbers, Count) ---
    # Add these *after* the user allergen check for potential prominence

    bad_ingredients = [i for i in classified_ingredients if i['status'] == 'bad']
    if bad_ingredients:
        count = len(bad_ingredients)
        warnings.append({
            'type': 'harmful_ingredients',
            'short_title': f'{count} Harmful Ingredients',
            'message': f"Contains {count} potentially harmful ingredients identified by AI.",
            'ingredients': [i['name'] for i in bad_ingredients]
        })

    caution_ingredients = [i for i in classified_ingredients if i['status'] == 'caution']
    if len(caution_ingredients) > settings.FS_WARN_MIN_CAUTION_INGREDIENTS:
        count = len(caution_ingredients)
        warnings.append({
            'type': 'caution_ingredients',
            'short_title': f'{count} Caution Ingredients',
            'message': f"Contains {count} ingredients marked for caution (e.g., synthetic, potential irritants).",
            'ingredients': [i['name'] for i in caution_ingredients]
        })

    e_number_pattern = r'\bE-?\d{3}[a-z]?\b'
    e_numbers = re.findall(e_number_pattern, ocr_text, re.IGNORECASE)
    if len(e_numbers) > settings.FS_WARN_MIN_E_NUMBERS:
        count = len(e_numbers)
        warnings.append({
            'type': 'many_additives',
            'short_title': f'{count} Additives (E-numbers)',
            'message': f"Contains {count} additives (E-numbers) detected in text.",
            'ingredients': e_numbers
        })

    if len(classified_ingredients) > settings.FS_WARN_MIN_TOTAL_INGREDIENTS:
        count = len(classified_ingredients)
        warnings.append({
            'type': 'many_ingredients',
            'short_title': f'{count} Ingredients Total',
            'message': f"Contains {count} ingredients - products with fewer ingredients are typically healthier.",
            'ingredients': []
        })

    return warnings 