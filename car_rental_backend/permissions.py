from rest_framework import permissions, serializers


class IsSuperUser(permissions.BasePermission):
    """Réservé aux super administrateurs (gestion globale agences/utilisateurs)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)


def check_same_agency(request, obj, field):
    """Refuse un objet lié appartenant à une autre agence (anti-IDOR).

    Les superusers ne sont pas restreints. Pour un utilisateur sans agence,
    tout objet rattaché à une agence est refusé.
    """
    if obj is None:
        return
    user = getattr(request, "user", None)
    if user is None or user.is_superuser:
        return
    obj_agency_id = getattr(obj, "agency_id", None)
    if obj_agency_id is not None and obj_agency_id != user.agency_id:
        raise serializers.ValidationError({field: "Objet introuvable."})


class IsAgencyStaff(permissions.BasePermission):
    """Personnel d'agence (OWNER/EMPLOYEE) ou superuser ; exclut les comptes CLIENT."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        return user.role != "CLIENT" and user.agency_id is not None
