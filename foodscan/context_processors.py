def auth0_session(request):
    """
    Adds the raw Auth0 user data from the session to the template context.
    Allows templates to check {% if session %} for logged-in state.
    """
    user_data = request.session.get('user')
    return {'session': user_data} 