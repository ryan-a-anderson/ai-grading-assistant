# OAuth Setup Guide

## Quick Start (Demo Mode)

The application works immediately with **email authentication**! You can:
1. Click "📧 Email Login/Register" on the landing page
2. Create an account with username, email, and password
3. Access the full grading functionality right away

## OAuth Configuration (Optional)

To enable Google and GitHub OAuth, follow these steps:

### Google OAuth Setup

1. **Go to Google Cloud Console**
   - Visit: https://console.cloud.google.com/
   - Create a new project or select an existing one

2. **Enable Google+ API**
   - Go to "APIs & Services" → "Library"
   - Search for "Google+ API" and enable it

3. **Create OAuth Credentials**
   - Go to "APIs & Services" → "Credentials"
   - Click "Create Credentials" → "OAuth 2.0 Client ID"
   - Choose "Web application"
   - Add authorized redirect URI: `http://localhost:8083/login/google/authorized`
   - Copy your Client ID and Client Secret

### GitHub OAuth Setup

1. **Go to GitHub Developer Settings**
   - Visit: https://github.com/settings/developers
   - Click "New OAuth App"

2. **Configure OAuth App**
   - Application name: "AI Grading Assistant"
   - Homepage URL: `http://localhost:8083`
   - Authorization callback URL: `http://localhost:8083/login/github/authorized`
   - Copy your Client ID and Client Secret

### Update Configuration

Replace the placeholder values in `app_oauth.py` (lines 25-28):

```python
# Replace these with your actual OAuth credentials
app.config['GOOGLE_OAUTH_CLIENT_ID'] = 'your-actual-google-client-id-here'
app.config['GOOGLE_OAUTH_CLIENT_SECRET'] = 'your-actual-google-client-secret-here'
app.config['GITHUB_OAUTH_CLIENT_ID'] = 'your-actual-github-client-id-here'
app.config['GITHUB_OAUTH_CLIENT_SECRET'] = 'your-actual-github-client-secret-here'
```

### Production Deployment

For production deployment, update the redirect URIs to match your domain:
- Google: `https://yourdomain.com/login/google/authorized`
- GitHub: `https://yourdomain.com/login/github/authorized`

## Current Status

✅ **Email Authentication** - Fully functional, no setup required
⚠️ **OAuth Buttons** - Ready for credentials (currently show setup pages)
✅ **All Core Features** - AI grading, dashboard, reports working
✅ **Professional UI** - Modern design with OAuth branding

## Testing the Application

1. **Use Email Auth**: Click "📧 Email Login/Register" for immediate access
2. **Create Account**: Register with username, email, password
3. **Test Grading**: Upload PDFs, create rubrics, get AI reports
4. **View History**: Access your complete assignment dashboard

The application is fully functional with email authentication - OAuth is an optional enhancement for user convenience!
