"""
users/views.py
───────────────
Auth endpoints:
  POST /api/v1/auth/login/    — returns access + refresh tokens
  POST /api/v1/auth/refresh/  — rotates refresh token
  POST /api/v1/auth/logout/   — blacklists refresh token
  GET  /api/v1/auth/me/       — returns current user profile
"""

from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .serializers import LoginSerializer, UserSerializer


def _token_response(user):
    """Generate a JWT token pair and return with user data."""
    refresh = RefreshToken.for_user(user)
    return {
        "success": True,
        "data": {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": UserSerializer(user).data,
        },
    }


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )

        if not user:
            return Response(
                {"success": False, "error": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"success": False, "error": "Account is deactivated."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(_token_response(user), status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Blacklist the refresh token so it cannot be reused after logout."""
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"success": False, "error": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            pass  # already invalid — treat as successful logout

        return Response({"success": True, "data": "Logged out."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the currently authenticated user's profile."""
        return Response({
            "success": True,
            "data": UserSerializer(request.user).data,
        })