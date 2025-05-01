from django.contrib.auth import authenticate, login, logout
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings # Import settings
import time
from jose import jwt
from jose.exceptions import JWTError, JWKError # Added JWKError
import requests # To fetch JWKS
import logging
from datetime import datetime, timezone # Added for manual expiry check

logger = logging.getLogger(__name__)

# Simple in-memory cache for JWKS with expiration
jwks_cache = {
    'keys': None,
    'expires': 0 # Timestamp when the cache expires
}

def get_jwks():
    """Fetches JWKS from Auth0, caching the result.

    Returns:
        The JWKS dictionary if successful, None otherwise.
    """
    global jwks_cache
    now = time.time()

    # Check if cached keys exist and are not expired
    if jwks_cache['keys'] and jwks_cache['expires'] > now:
        logger.debug("Using cached JWKS.")
        return jwks_cache['keys']

    # Fetch fresh JWKS
    jwks_url = f"https://{settings.AUTH0_DOMAIN}/.well-known/jwks.json"
    logger.info(f"Fetching fresh JWKS from {jwks_url}")
    try:
        response = requests.get(jwks_url, timeout=10) # Added timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        jwks = response.json()

        # Update cache with new keys and expiration (e.g., 1 hour)
        cache_duration = 3600 # 1 hour, adjust as needed
        jwks_cache['keys'] = jwks
        jwks_cache['expires'] = now + cache_duration
        logger.info(f"Successfully fetched and cached JWKS for {cache_duration} seconds.")
        return jwks
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching JWKS from {jwks_url}: {e}", exc_info=True)
        # Reset cache on error to force retry on next request
        jwks_cache = {'keys': None, 'expires': 0}
        return None
    except Exception as e:
         logger.error(f"Unexpected error processing JWKS: {e}", exc_info=True)
         # Reset cache on error
         jwks_cache = {'keys': None, 'expires': 0}
         return None


class Auth0Middleware(MiddlewareMixin):
    """Django Middleware to handle Auth0 authentication.

    Checks for Auth0 user data in the session, validates the access token,
    logs the user out if the token is invalid, and uses the Auth0Backend
    to log the user into Django if they aren't already.
    """
    def process_request(self, request):
        # Check for Auth0 user data stored by the callback view
        user_data = request.session.get('user')

        if not user_data:
            # No Auth0 user data in session.
            # If user is somehow authenticated in Django without Auth0 session data,
            # maybe log them out? Or trust the Django session?
            # For now, we just let the request proceed without Auth0 validation.
            return None

        # --- Always Validate Access Token if user_data exists ---
        access_token = user_data.get('access_token')

        if not access_token:
            logger.warning("Auth0 user data found in session, but missing 'access_token'. Clearing session and logging out.")
            if hasattr(request, 'user') and request.user.is_authenticated:
                 logout(request)
            request.session.clear()
            return None

        try:
            # --- Manual Expiry Check ---
            try:
                unverified_claims = jwt.get_unverified_claims(access_token)
                exp_timestamp = unverified_claims.get('exp')

                if not exp_timestamp:
                    logger.warning("Token is missing 'exp' claim. Treating as invalid.")
                    raise JWTError("Missing 'exp' claim")

                current_utc_timestamp = datetime.now(timezone.utc).timestamp()

                if exp_timestamp < current_utc_timestamp:
                    logger.warning(f"Manual check failed: Token expired at {datetime.fromtimestamp(exp_timestamp, timezone.utc)} (exp={exp_timestamp}), current time is {datetime.now(timezone.utc)} (now={current_utc_timestamp}).")
                    raise JWTError("Token has expired (Manual Check)")
                else:
                    logger.debug(f"Manual check passed: Token expiry {datetime.fromtimestamp(exp_timestamp, timezone.utc)} is in the future.")
            except JWTError as manual_err: # Catch errors specifically from getting claims/manual check
                raise manual_err # Re-raise to be caught by the outer handler
            except Exception as inner_e: # Catch other unexpected errors during manual check
                 logger.error(f"Unexpected error during manual token check: {inner_e}", exc_info=True)
                 raise JWTError(f"Manual Check Error: {inner_e}") # Raise JWTError to trigger logout
            # --- End Manual Expiry Check ---

            # If manual check passes, proceed with full validation by jwt.decode
            jwks = get_jwks()
            if not jwks:
                 logger.error("Cannot validate access token: Failed to fetch JWKS. Clearing session and logging out.")
                 if hasattr(request, 'user') and request.user.is_authenticated:
                      logout(request)
                 request.session.clear()
                 return None

            issuer_url = f'https://{settings.AUTH0_DOMAIN}/'
            # This will validate signature, audience, issuer, and expiry again
            payload = jwt.decode(
                access_token,
                jwks,
                algorithms=['RS256'],
                audience=settings.AUTH0_AUDIENCE,
                issuer=issuer_url
            )
            # Token is valid (signature, expiry, audience, issuer)
            logger.debug("Auth0 Access Token successfully validated by jwt.decode.")

            # If token is valid, but user is NOT logged into Django yet, log them in.
            if not (hasattr(request, 'user') and request.user.is_authenticated):
                logger.debug("Django user not authenticated, attempting login via Auth0Backend.")
                user = authenticate(request=request, token=user_data)
                if user:
                    login(request, user)
                    logger.debug(f"Django user {user.username} logged in via Auth0Middleware.")
                else:
                     # Token was valid, but backend failed
                     logger.error(f"Access Token OK, but Auth0Backend failed for sub: {payload.get('sub')}. Clearing session.")
                     # No need to logout() as user wasn't logged in
                     request.session.clear()
            # else:
                # Token is valid AND user is already logged in via Django session. Do nothing.
                # logger.debug("Token valid and Django user already authenticated.")

        except (JWTError, JWKError) as e:
            # Token validation failed (expired, bad signature, etc.)
            logger.warning(f"Auth0 Access Token validation failed: {e}. Clearing session and logging out.")
            if hasattr(request, 'user') and request.user.is_authenticated:
                 logout(request)
            request.session.clear()
        except Exception as e:
            logger.error(f"Unexpected error during Access Token validation: {e}. Clearing session and logging out.", exc_info=True)
            if hasattr(request, 'user') and request.user.is_authenticated:
                 logout(request)
            request.session.clear()

        return None