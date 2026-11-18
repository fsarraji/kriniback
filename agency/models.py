from django.contrib.auth.models import AbstractUser
from django.db import models

from car_rental_backend.fields import EncryptedCharField
from car_rental_backend.uploads import agency_cachet_upload_to, agency_logo_upload_to
from car_rental_backend.validators import validate_image_size


# 1. Modèle de l'agence (Société)
class Agency(models.Model):
    nom_agence = models.CharField(max_length=100, verbose_name="Nom de l'agence")
    adresse = models.TextField(verbose_name="Adresse")
    ville = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Ville"
    )
    telephone = models.CharField(max_length=20, verbose_name="Téléphone")
    rc = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Registre du Commerce (RC)"
    )
    ice = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Identifiant Commun de l'Entreprise (ICE)",
    )
    email = models.EmailField(blank=True, null=True)
    logo = models.ImageField(
        upload_to=agency_logo_upload_to,
        null=True,
        blank=True,
        validators=[validate_image_size],
    )
    cachet_signature = models.ImageField(
        upload_to=agency_cachet_upload_to,
        null=True,
        blank=True,
        verbose_name="Cachet et Signature",
        validators=[validate_image_size],
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True, verbose_name="Compte actif")
    caution_active = models.BooleanField(default=True, verbose_name="Caution active")
    caution_montant = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1500.00,
        verbose_name="Montant de la caution",
    )

    # Kilométrage journalier inclus
    km_extra_active = models.BooleanField(
        default=True, verbose_name="Facturation km extra active"
    )
    km_par_jour = models.IntegerField(default=250, verbose_name="Km inclus par jour")
    km_tarif_extra_defaut = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=1.50,
        verbose_name="Tarif DH par km supplémentaire (défaut)",
    )

    # Délai de confort (en heures) : minutes de préparation/nettoyage avant et
    # après chaque location, pendant lesquelles la voiture reste indisponible.
    location_buffer_hours = models.DecimalField(
        max_digits=4,
        decimal_places=0,
        default=2,
        verbose_name="Délai de confort (heures)",
        help_text="Heures d'indisponibilité supplémentaires avant et après chaque location.",
    )

    # TVA appliquée aux factures (les prix des contrats sont TTC)
    tva_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=20.00,
        verbose_name="Taux de TVA (%)",
    )

    # Marques affichées dans les formulaires (vide = toutes les marques)
    brands = models.ManyToManyField(
        "fleet.Brand",
        related_name="agencies",
        blank=True,
        verbose_name="Marques affichées",
    )

    # Suivi GPS Traccar : chaque agence dispose de son propre compte serveur
    traccar_url = models.CharField(
        max_length=200, blank=True, null=True, verbose_name="URL serveur Traccar"
    )
    traccar_username = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Utilisateur Traccar"
    )
    traccar_password = EncryptedCharField(
        max_length=500, blank=True, null=True, verbose_name="Mot de passe Traccar"
    )

    def __str__(self):
        return self.nom_agence


# 1b. Modèle de l'abonnement SaaS de l'agence
class Subscription(models.Model):
    PLAN_CHOICES = (
        ("GRATUIT", "Gratuit"),
        ("BASIC", "Basic"),
        ("PRO", "Pro"),
        ("PREMIUM", "Premium"),
    )

    STATUS_CHOICES = (
        ("ACTIVE", "Actif"),
        ("EXPIRED", "Expiré"),
        ("SUSPENDED", "Suspendu"),
        ("CANCELLED", "Annulé"),
    )

    agency = models.ForeignKey(
        Agency,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name="Agence",
    )
    plan = models.CharField(
        max_length=20, choices=PLAN_CHOICES, default="BASIC", verbose_name="Plan"
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Prix mensuel (DH)"
    )
    start_date = models.DateField(verbose_name="Date de début")
    end_date = models.DateField(verbose_name="Date de fin")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="ACTIVE", verbose_name="Statut"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Abonnement"
        verbose_name_plural = "Abonnements"

    @property
    def is_current(self):
        from datetime import date

        return (
            self.status == "ACTIVE" and self.start_date <= date.today() <= self.end_date
        )

    def __str__(self):
        return f"{self.agency.nom_agence} - {self.plan}"


# 2. نموذج المستخدم المخصص المرتبط بالوكالة
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ("OWNER", "Gérant / Propriétaire"),
        ("EMPLOYEE", "Employé"),
        ("CLIENT", "Client"),
    )

    # link user to agency
    agency = models.ForeignKey(
        Agency, on_delete=models.CASCADE, related_name="users", null=True, blank=True
    )
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default="EMPLOYEE", verbose_name="Rôle"
    )

    def __str__(self):
        return f"{self.username} - {self.agency.nom_agence if self.agency else 'Super Admin'}"


class Notification(models.Model):
    """Alerte interne d'une agence (assurance, vidange, retards...)."""

    KIND_CHOICES = (
        ("INSURANCE", "Assurance"),
        ("VISITE", "Visite technique"),
        ("VIDANGE", "Vidange"),
        ("RETURN_DUE", "Retour proche"),
        ("RETURN_OVERDUE", "Retour en retard"),
        ("GEOFENCE", "Hors zone autorisée"),
    )
    LEVEL_CHOICES = (("INFO", "Info"), ("WARNING", "Attention"), ("CRITICAL", "Urgent"))

    agency = models.ForeignKey(
        Agency, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="WARNING")
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    # Code + paramètres : permettent de rendre le texte dans la langue du lecteur
    # (voir alert_texts.py). Vides pour les anciennes alertes : title/message font foi.
    code = models.CharField(max_length=30, blank=True, default="")
    params = models.JSONField(blank=True, default=dict)
    # Clé de déduplication : une même alerte n'est créée qu'une fois.
    dedup_key = models.CharField(max_length=120)
    is_read = models.BooleanField(default=False)
    emailed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "dedup_key"], name="notif_agency_dedup_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["agency", "is_read"], name="notif_agency_read_idx")
        ]

    def __str__(self):
        return f"[{self.kind}] {self.title}"
