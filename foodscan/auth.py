import json
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.urls import reverse
from urllib.parse import quote_plus, urlencode
import logging

logger = logging.getLogger(__name__)

oauth = OAuth()

oauth.register(
    "auth0",
    client_id=settings.AUTH0_CLIENT_ID,
    client_secret=settings.AUTH0_CLIENT_SECRET,
    client_kwargs={
        "scope": "openid profile email",
    },
    server_metadata_url=f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration",
)

def get_login_url(request):
    """Generates the Auth0 Universal Login URL.

    Args:
        request: The Django HttpRequest object.

    Returns:
        The URL to redirect the user to for Auth0 login.
    """
    return oauth.auth0.authorize_redirect(
        request, 
        request.build_absolute_uri(reverse("callback")),
        audience=settings.AUTH0_AUDIENCE
    )

def get_logout_url(request):
    """Generates the Auth0 logout URL.

    Args:
        request: The Django HttpRequest object.

    Returns:
        The URL to redirect the user to for Auth0 logout.
    """
    return f"https://{settings.AUTH0_DOMAIN}/v2/logout?" + urlencode(
        {
            "returnTo": request.build_absolute_uri(reverse("index")),
            "client_id": settings.AUTH0_CLIENT_ID,
        },
        quote_via=quote_plus,
    )

def get_token_from_code(request):
    """Exchanges the authorization code for an access token after callback.

    Args:
        request: The Django HttpRequest object containing the callback parameters.

    Returns:
        The access token dictionary if successful, None otherwise.
    """
    try:
        return oauth.auth0.authorize_access_token(request)
    except Exception as e:
        logger.error(f"Error exchanging authorization code for token: {e}", exc_info=True)
        return None 