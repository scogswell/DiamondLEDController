"""
Settings for secrets: ssid, password, twitch_client_id, twitch_client_secret
"""
# This file is where you keep secret settings, passwords, and tokens!
# If you put them in the code you risk committing that info or sharing it

# To get and generate the twitch_client_id and twitch_client_secret: 
# https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#oauth-client-credentials-flow
# https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow
# Register a new app with:  
#  https://dev.twitch.tv/docs/authentication/register-app/
# Logging into your twitch dev console https://dev.twitch.tv/console
# Register your app as category "other", and use "http://localhost" for the oauth callback. 
# Generate a new secret and copy/paste the id and secret into the variables below:  

secrets = {
    'ssid' : 'Your-ssid',
    'password' : 'your-password',
    'twitch_client_id': 'your-twitch-client',
    'twitch_client_secret': 'your-twitch-client-secret'
    }
