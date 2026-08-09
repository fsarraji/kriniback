from datetime import date

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from django.utils import timezone


def agency_has_active_subscription(agency_id):
    """Une agence est autorisée si elle a au moins un abonnement ACTIVE dans sa période de validité."""
    if not agency_id:
        return True
    from .models import Subscription
    return Subscription.objects.filter(
        agency_id=agency_id,
        status='ACTIVE',
        start_date__lte=timezone.localdate(),
        end_date__gte=timezone.localdate(),
    ).exists()


class SubscriptionJWTAuthentication(JWTAuthentication):
    """Bloque l'accès API aux utilisateurs dont l'agence n'a pas d'abonnement actif."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if user.agency_id is None:
            return user
        if not agency_has_active_subscription(user.agency_id):
            raise AuthenticationFailed(
                "Votre agence ne dispose pas d'un abonnement actif.",
                code="no_active_subscription",
            )
        return user
