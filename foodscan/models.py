from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
import json
import logging

logger = logging.getLogger(__name__)

# Helper function for the default value of UserProfile.allergens
def default_allergens():
    # Provides a default structure for allergens, separating standard list from custom text.
    return {"standard": [], "custom": ""}

class UserProfile(models.Model):
    # Stores user-specific settings and preferences.
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    dietary_preferences = models.JSONField(default=dict) # e.g., {'vegetarian': True, 'gluten_free': False}
    allergens = models.JSONField(default=default_allergens) # Stores standard and custom allergens
    daily_calorie_goal = models.PositiveIntegerField(null=True, blank=True, verbose_name="Daily Calorie Goal (kcal)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

class ScanHistory(models.Model):
    # Records each scan event performed by a user.
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    image = models.CharField(max_length=255, null=True, blank=True)  # Deprecated? Identifier/path for original ingredients image.
    barcode = models.CharField(max_length=50, null=True, blank=True, db_index=True) # Decoded barcode value, if available.
    product_name = models.CharField(max_length=255, null=True, blank=True) # Product name from OFF or other sources.
    product_image_url = models.URLField(max_length=500, null=True, blank=True) # Image URL from OFF.
    nutritional_data = models.JSONField(null=True, blank=True) # Structured nutrition facts (e.g., from OFF nutriments).
    source = models.CharField(max_length=20, default='unknown') # Origin of the scan (e.g., 'barcode_off', 'ocr', 'manual').
    extracted_text = models.TextField() # Raw text (from OCR, OFF ingredients_text, or manual input).
    health_score = models.IntegerField() # Calculated score based on analysis.
    # Stores the structured result from the AI ingredient analysis.
    # Expected JSON format: {"ingredients": [...], "warnings": [...], ...}
    analysis_result = models.TextField(default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        display_name = self.product_name if self.product_name else f"Scan {self.id}"
        return f"{display_name} by {self.user.username} on {self.created_at.strftime('%Y-%m-%d')}"
    
    @property
    def ingredients(self):
        """Return parsed ingredients from analysis_result"""
        if not self.analysis_result:
            return []
        try:
            data = json.loads(self.analysis_result)
            if isinstance(data, dict):
                return data.get('ingredients', [])
            else:
                 logger.warning(f"ScanHistory {self.id}: analysis_result is not a dictionary: {self.analysis_result}")
                 return []
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding analysis_result for ScanHistory {self.id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error parsing ingredients for ScanHistory {self.id}: {e}")
            return []
    
    @property
    def warnings(self):
        """Return warnings from analysis_result"""
        if not self.analysis_result:
            return []
        try:
            data = json.loads(self.analysis_result)
            if isinstance(data, dict):
                return data.get('warnings', [])
            else:
                 logger.warning(f"ScanHistory {self.id}: analysis_result is not a dictionary: {self.analysis_result}")
                 return []
        except json.JSONDecodeError as e:
             logger.error(f"Error decoding analysis_result for ScanHistory {self.id}: {e}")
             return []
        except Exception as e:
            logger.error(f"Unexpected error parsing warnings for ScanHistory {self.id}: {e}")
            return []

class IngredientInfo(models.Model):
    # Stores reference information about specific ingredients.
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField()
    health_impact = models.TextField()
    is_harmful = models.BooleanField(default=False)
    alternatives = models.JSONField(default=list)

    def __str__(self):
        return self.name 

# --- New Calorie Tracking Model --- 
class CalorieEntry(models.Model):
    # Records individual calorie intake entries for users.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='calorie_entries')
    date = models.DateField(default=timezone.now)
    calories = models.IntegerField()
    notes = models.TextField(blank=True, null=True) # Optional notes for the entry
    timestamp = models.DateTimeField(auto_now_add=True) # When the entry was created

    class Meta:
        verbose_name_plural = "Calorie Entries"
        ordering = ['-date', '-timestamp'] # Show newest entries first by default

    def __str__(self):
        return f"{self.user.username} - {self.date}: {self.calories} kcal" 