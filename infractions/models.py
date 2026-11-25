from django.db import models
from django.utils import timezone

from agency.models import Agency
from car_rental_backend.uploads import contract_signed_upload_to
from car_rental_backend.validators import validate_signed_contract
from clients.models import Client
from contracts.models import Contract
from fleet.models import Vehicle


class Infraction(models.Model):
    """Infraction routière à déclarer (identification du conducteur) sur le portail NARSA."""

    PENDING = "pending"  # saisie, pas encore rapprochée
    MATCHED = "matched"  # contrat unique trouvé
    NO_CONTRACT = "no_contract"  # aucun contrat / plusieurs contrats : revue manuelle
    QUEUED = "queued"  # envoyée au worker
    SUBMITTED = "submitted"  # déclarée sur NARSA
    FAILED = "failed"

    STATUS_CHOICES = [
        (PENDING, "En attente"),
        (MATCHED, "Rapprochée"),
        (NO_CONTRACT, "Sans contrat unique"),
        (QUEUED, "En file"),
        (SUBMITTED, "Déclarée"),
        (FAILED, "Échec"),
    ]

    agency = models.ForeignKey(
        Agency, on_delete=models.CASCADE, related_name="infractions"
    )
    reference_pv = models.CharField(max_length=60, verbose_name="Référence PV")
    matricule = models.CharField(max_length=30)
    date_infraction = models.DateTimeField()
    lieu = models.CharField(max_length=255, blank=True, default="")
    montant = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    contract = models.ForeignKey(
        Contract, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    error = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Contrat signé fourni au moment de déclarer : simple transit vers NARSA, supprimé après envoi.
    signed_contract = models.FileField(
        upload_to=contract_signed_upload_to,
        validators=[validate_signed_contract],
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-date_infraction", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "reference_pv"], name="infraction_agency_pv_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["agency", "status"], name="infraction_ag_st_idx")
        ]

    def __str__(self):
        return f"{self.reference_pv} ({self.matricule})"

    def discard_signed_contract(self):
        """Supprime le fichier du stockage et vide le champ (aucune conservation après envoi)."""
        if self.signed_contract:
            self.signed_contract.delete(save=False)
        self.signed_contract = None

    def mark_submitted(self):
        self.status = self.SUBMITTED
        self.error = ""
        self.submitted_at = timezone.now()
        self.discard_signed_contract()
        self.save(update_fields=["status", "error", "submitted_at", "signed_contract"])

    def delete(self, *args, **kwargs):
        if self.signed_contract:
            self.signed_contract.delete(save=False)
        return super().delete(*args, **kwargs)
