# FoodScan - AI-Powered Ingredient Scanner & Calorie Tracker

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Django Version](https://img.shields.io/badge/django-4.2-blue.svg)](https://www.djangoproject.com/)

## Live Demo

Check out the live version of this project hosted at: **[https://bejerital.pythonanywhere.com](https://bejerital.pythonanywhere.com)**

## Overview

FoodScan is a web application designed to help users understand the ingredients in their food products and track their daily calorie intake. By scanning barcodes or uploading images of ingredient lists, users can leverage AI (powered by Google Gemini) and the Open Food Facts database to get detailed analysis, health scores, warnings, and nutritional information. It features secure user profile management and authentication via **Auth0, demonstrating practical application of modern identity and access management (IAM) principles**, alongside a comprehensive calorie tracking calendar.

## Key Features

*   **Barcode Scanning:** Upload an image containing a product barcode to automatically fetch product details (name, image, ingredients, nutrition) from Open Food Facts.
*   **Ingredient OCR:** Upload an image of a product's ingredient list. The application uses Google Gemini Vision for Optical Character Recognition (OCR) to extract the text.
*   **AI Ingredient Analysis (Gemini):**
    *   Extracts and deduplicates ingredients from OCR text, handling multiple languages.
    *   Classifies each ingredient based on potential health impact (good, bad, caution, neutral).
    *   Determines dietary compatibility flags (vegetarian, vegan, gluten-free).
    *   Provides English translations and descriptions.
*   **Nutritional Data:** Displays nutritional information (calories, fat, sugar, salt, etc.) obtained from Open Food Facts or extracted via AI from OCR text.
*   **Health Score:** Calculates a score (0-100) based on ingredient classifications and nutritional data.
*   **Warnings:** Generates warnings based on:
    *   Potentially harmful ingredients.
    *   User-defined allergens.
    *   Conflicts with user dietary preferences (vegetarian, vegan, gluten-free, sugar-free).
    *   High levels of saturated fat, sugar, or salt.
*   **Scan History:** Users can view their past scans, filter by date or scan ID, and revisit results.
*   **User Profiles:**
    *   Authentication managed via Auth0.
    *   Users can set dietary preferences (vegetarian, vegan, etc.).
    *   Users can specify standard and custom allergens.
    *   Users can set a daily calorie goal.
*   **Calorie Tracker:**
    *   Interactive calendar view.
    *   Add, edit, and delete daily calorie entries with optional notes.
    *   Visualizes daily totals against the user's goal.
    *   Displays monthly and rolling 7/30-day average calorie intake.

## Technology Stack

*   **Backend:** Python 3.10+, Django 4.2+
*   **Database:** SQLite (default), PostgreSQL/MySQL recommended for production
*   **Authentication:** **Auth0 (Leveraged for secure user authentication, demonstrating robust IAM integration)**
*   **AI Services:** Google Gemini API (for ingredient analysis, nutrition extraction, OCR)
*   **Food Database:** Open Food Facts (via `openfoodfacts-python` SDK and direct API fallback)
*   **Barcode Reading:** `pyzbar`, `opencv-python-headless`
*   **Image Processing:** Pillow
*   **Environment Variables:** `python-dotenv`
*   **Frontend:** HTML, CSS, JavaScript (potentially with a framework like Bootstrap)
*   **Containerization:** Docker (optional)

## Local Development Setup

1.  **Prerequisites:**
    *   Python (3.10, 3.11, or 3.12 recommended)
    *   `pip` (Python package installer)
    *   Git

2.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd <repository-directory>
    ```

3.  **Create and Activate Virtual Environment:**
    ```bash
    # Windows
    python -m venv venv
    .\venv\Scripts\activate

    # macOS / Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *(Note: For stable production environments, consider pinning versions using `pip freeze > requirements.txt` after setup.)*

5.  **Configure Environment Variables:**
    *   Copy the `.env.example` file to a new file named `.env`:
        ```bash
        # Windows (Command Prompt)
        copy .env.example .env
        # Windows (PowerShell)
        Copy-Item .env.example .env
        # macOS / Linux
        cp .env.example .env
        ```
 
6.  **Apply Database Migrations:**
    ```bash
    python manage.py migrate
    ```

7.  **Run the Development Server:**
    ```bash
    python manage.py runserver
    ```
    By default, the application should be accessible at `http://127.0.0.1:8000` or `http://localhost:8000`.

## Deployment

Deploying a Django application involves several steps. Here are general guidelines:

1.  **Choose a Hosting Provider:** Options include Platform-as-a-Service (PaaS) like Heroku, Render, PythonAnywhere, or Virtual Private Servers (VPS) from providers like AWS, Google Cloud, Azure, DigitalOcean.
2.  **Production Settings:**
    *   Set `DEBUG = False` in your settings (controlled via the `DJANGO_DEBUG` environment variable).
    *   Configure `ALLOWED_HOSTS` with your domain name(s) (via `DJANGO_ALLOWED_HOSTS`).
    *   Generate and use a strong, unique `SECRET_KEY` (via `DJANGO_SECRET_KEY`).
    *   Configure a production-grade database (PostgreSQL, MySQL) and set the `DATABASE_URL` environment variable.
3.  **Static Files:** Run `python manage.py collectstatic` and configure your web server (e.g., Nginx, Apache) or hosting provider to serve static files from the collected directory (`STATIC_ROOT`).
4.  **WSGI Server:** Use a production WSGI server like Gunicorn or uWSGI to run your Django application.
5.  **Environment Variables:** Securely provide all required environment variables (API keys, database credentials, Auth0 details, etc.) to your production environment. **Do not hardcode secrets.**
6.  **HTTPS:** Ensure your site is served over HTTPS.

*   **Docker:** A `Dockerfile` is provided for containerizing the application, which can simplify deployment to container orchestration platforms (like Kubernetes) or services that support Docker images.
*   **Platform-Specific Guides:** Refer to the documentation of your chosen hosting provider for detailed Django deployment instructions (e.g., [Deploying Django | Django documentation](https://docs.djangoproject.com/en/stable/howto/deployment/), [Deploying Django on Heroku](https://devcenter.heroku.com/articles/deploying-python)).

## Contributing

Contributions are welcome! Please refer to the contribution guidelines (if available) or feel free to submit pull requests or open issues for bugs and feature requests.

## License

This project is licensed under the MIT License 
