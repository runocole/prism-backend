"""
users/serializers.py
─────────────────────
Serializers for auth responses and user profile.
"""

from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Read-only profile data returned after login or from /me."""

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "role", "date_joined")
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    """Validates email + password on login."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)