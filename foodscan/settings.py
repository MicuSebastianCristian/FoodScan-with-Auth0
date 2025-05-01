import os
from pathlib import Path
from dotenv import load_dotenv # Import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file (especially for development)
load_dotenv(os.path.join(BASE_DIR, '.env'))

# SECURITY WARNING: keep the secret key used in production secret!
# Load from env var, use insecure default ONLY for local dev if not set.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-foodscan-development-key')

# SECURITY WARNING: don't run with debug turned on in production!
# Load from env var, default to False (production). Set DJANGO_DEBUG=True in .env for local dev.
DEBUG = os.environ.get('DJANGO_DEBUG', 'False') == 'True'

# Load allowed hosts from env var (comma-separated string), default to localhost for dev.
# Production MUST set this variable with the correct domain(s).
ALLOWED_HOSTS_STRING = os.environ.get('DJANGO_ALLOWED_HOSTS', 'bejerital.pythonanywhere.com')
ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS_STRING.split(',') if host.strip()]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'foodscan',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'foodscan.middleware.Auth0Middleware',
]

ROOT_URLCONF = 'foodscan.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'foodscan.context_processors.auth0_session',
            ],
        },
    },
]

WSGI_APPLICATION = 'foodscan.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.0/ref/settings/#databases
# NOTE: Consider using dj-database-url library to configure DB from DATABASE_URL env var.
#       Example: import dj_database_url
#                DATABASES = {'default': dj_database_url.config(default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')}
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        # WARNING: sqlite3 is not recommended for production due to concurrency limitations.
        #          Use PostgreSQL or MySQL in production.
    }
}

# Authentication Backends
# Specifies the methods Django uses to authenticate users.
AUTHENTICATION_BACKENDS = [
    'foodscan.backends.Auth0Backend', # Custom backend for Auth0 authentication
    'django.contrib.auth.backends.ModelBackend', # Default Django user model backend
]

# Password validation
# https://docs.djangoproject.com/en/4.0/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/4.0/topics/i18n/
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.0/howto/static-files/
STATIC_URL = '/static/'
# Directory where `collectstatic` gathers all static files for deployment.
# Ensure your production web server (e.g., Nginx) is configured to serve files from this path.
# Alternatively, consider using Whitenoise for simpler static file serving directly from Django.
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# Media files (User-uploaded content)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media') # Directory to store user uploads

# Default primary key field type
# https://docs.djangoproject.com/en/4.0/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Third-Party API Configuration ---
# Load all sensitive keys/secrets from environment variables.
# Provide placeholder defaults ONLY for local development awareness;
# these services likely won't work without real keys even locally.

# OCR Service Settings (OCR.space)
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY', '') # Real key needed even for dev testing
OCR_SPACE_API_URL = os.environ.get('OCR_SPACE_API_URL', 'https://api.ocr.space/parse/image')

# Auth0 Authentication Settings
AUTH0_CLIENT_ID = os.environ.get('AUTH0_CLIENT_ID', '') # Real key needed
AUTH0_CLIENT_SECRET = os.environ.get('AUTH0_CLIENT_SECRET', '') # Real key needed
AUTH0_DOMAIN = os.environ.get('AUTH0_DOMAIN', '') # Real domain needed
# Default callback for local dev, override with DJANGO_AUTH0_CALLBACK_URL in production.
AUTH0_CALLBACK_URL = os.environ.get('DJANGO_AUTH0_CALLBACK_URL', 'bejerital.pythonanywhere.com/callback')
# Default audience, override with DJANGO_AUTH0_AUDIENCE if needed.
AUTH0_AUDIENCE = os.environ.get('DJANGO_AUTH0_AUDIENCE', 'https://food-scan-api')

# AI Ingredient Analyzer API Key (e.g., Google Gemini)
AI_ANALYZER_API_KEY = os.environ.get('AI_ANALYZER_API_KEY', '') # Real key needed

# --- Django Application Settings ---

# Login URL (Where @login_required redirects)
LOGIN_URL = '/login/'

# --- FoodScan Analysis Configuration ---

# Scoring parameters (used in services.ingredient_analyzer.calculate_score)
FS_SCORE_BASE = 90.0
FS_SCORE_POINTS_GOOD = 1.5
FS_SCORE_POINTS_BAD = -18.0
FS_SCORE_POINTS_CAUTION = -8.0
FS_SCORE_POINTS_NEUTRAL = 0.0

# Nutritional Score Adjustments (Thresholds & Points)
FS_SCORE_NUTRITION = {
    'SAT_FAT_PENALTY_THRESHOLDS': [15.0, 8.0, 4.0], # High -> Medium -> Low
    'SAT_FAT_PENALTY_POINTS': [-12.0, -8.0, -4.0], # Points for above thresholds
    'SUGARS_PENALTY_THRESHOLDS': [25.0, 15.0, 5.0],
    'SUGARS_PENALTY_POINTS': [-15.0, -10.0, -5.0],
    'SALT_PENALTY_THRESHOLDS': [2.0, 1.25, 0.6],
    'SALT_PENALTY_POINTS': [-10.0, -6.0, -3.0],
    'FIBER_BONUS_THRESHOLDS': [6.0, 3.0], # High -> Medium
    'FIBER_BONUS_POINTS': [4.0, 2.0],
    'PROTEIN_BONUS_THRESHOLDS': [20.0, 10.0],
    'PROTEIN_BONUS_POINTS': [5.0, 2.0],
}

# Nutritional Warning Thresholds (used in services.ingredient_analyzer.generate_warnings)
FS_WARN_THRESHOLD_SAT_FAT = 5.0
FS_WARN_THRESHOLD_SUGARS = 10.0
FS_WARN_THRESHOLD_SALT = 1.5

# Other Warning Thresholds (used in services.ingredient_analyzer.generate_warnings)
FS_WARN_MIN_CAUTION_INGREDIENTS = 4 # Min count of 'caution' ingredients to trigger warning
FS_WARN_MIN_E_NUMBERS = 4           # Min count of E-numbers to trigger warning
FS_WARN_MIN_TOTAL_INGREDIENTS = 16 # Min count of total ingredients to trigger warning