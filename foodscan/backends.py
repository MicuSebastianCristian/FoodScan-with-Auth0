import requests
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User
from django.conf import settings
from jose import jwt
from jose.exceptions import JWTError
import logging

logger = logging.getLogger(__name__)

class Auth0Backend(BaseBackend):
    """Custom Django authentication backend for Auth0.

    Authenticates users based on the ID token provided by Auth0 in the session.
    Creates or updates Django User objects based on Auth0 user information.
    """
    def authenticate(self, request, token=None):
        """Authenticates the user using the Auth0 ID token.

        This method is called by Django's authentication framework.

        Args:
            request: The Django HttpRequest object.
            token: The Auth0 token dictionary (containing 'id_token') stored 
                   in the session by the Auth0Middleware.

        Returns:
            The authenticated Django User object if successful, None otherwise.
        """
        if token is None or 'id_token' not in token:
            logger.debug("Auth0Backend: No id_token found in token data.")
            return None

        id_token = token['id_token']
        # Construct the JWKS URL from Auth0 domain in settings
        jwks_url = f"https://{settings.AUTH0_DOMAIN}/.well-known/jwks.json"
        
        try:
            # Fetch the JWKS from Auth0
            jwks = requests.get(jwks_url).json()
            
            # Decode and validate the JWT (ID token)
            # This verifies the signature, audience, issuer, and expiration
            payload = jwt.decode(
                id_token,
                jwks, # Public keys to verify signature
                algorithms=["RS256"], # Algorithm used by Auth0
                audience=settings.AUTH0_CLIENT_ID, # Should be client_id for ID tokens
                issuer=f"https://{settings.AUTH0_DOMAIN}/"
            )

            # Use 'sub' (subject) claim as the unique identifier for the user
            auth0_user_id = payload.get('sub')
            if not auth0_user_id:
                 logger.warning("Auth0Backend: 'sub' claim missing in ID token payload.")
                 return None

            # Try to find an existing user or create a new one
            user, created = User.objects.get_or_create(
                username=auth0_user_id, # Map Auth0 'sub' to Django username
                defaults={
                    'email': payload.get('email'),
                    'first_name': payload.get('name', ''), # Use name claim, fallback to empty
                    # 'last_name': payload.get('family_name', ''), # Example if needed
                }
            )

            if created:
                # Set unusable password for externally authenticated users
                user.set_unusable_password()
                user.save()
                logger.info(f"Auth0Backend: Created new user for Auth0 ID: {auth0_user_id}")
            else:
                # Optionally update user fields if they changed in Auth0
                # Example: Update email if different
                needs_save = False
                if user.email != payload.get('email'):
                    user.email = payload.get('email')
                    needs_save = True
                if user.first_name != payload.get('name'):
                     user.first_name = payload.get('name', '')
                     needs_save = True
                # Add other fields to update if necessary
                
                if needs_save:
                    user.save()
                    logger.info(f"Auth0Backend: Updated user details for Auth0 ID: {auth0_user_id}")

            return user

        except JWTError as e:
            logger.error(f"Auth0Backend: JWT validation/decoding error: {e}", exc_info=True)
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Auth0Backend: Error fetching JWKS from {jwks_url}: {e}", exc_info=True)
            return None
        except Exception as e:
             logger.error(f"Auth0Backend: Unexpected error during authentication: {e}", exc_info=True)
             return None

    def get_user(self, user_id):
        """Retrieves a user instance based on the user ID (primary key).

        Required method for Django authentication backends.

        Args:
            user_id: The primary key of the User object.

        Returns:
            The User object if found, None otherwise.
        """
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logger.warning(f"Auth0Backend: User with pk={user_id} not found.")
            return None 