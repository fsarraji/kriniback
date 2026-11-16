from django.db import models, transaction
from django.utils import timezone

from agency.models import Agency, CustomUser
from contracts.models import Contract


class Payment(models.Model):
    PAYMENT_METHODS = [
        ("Espèce", "Espèce (Cash)"),
        ("Chèque", "Chèque"),
        ("Virement", "Virement (Bank Transfer)"),
        ("TPE", "TPE (Card)"),
    ]

    agency = models.ForeignKey(
        Agency,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="الوكالة",
    )
    contract = models.ForeignKey(
        Contract,
        on_delete=models.CASCADE,
        related_name="payments_history",
        verbose_name="العقد",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, verbose_name="الموظف المستلم"
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    payment_method = models.CharField(
        max_length=50,
        choices=PAYMENT_METHODS,
        default="Espèce",
        verbose_name="طريقة الدفع",
    )
    reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="المرجع (رقم الشيك أو التحويل)",
    )
    payment_date = models.DateTimeField(
        default=timezone.now, verbose_name="تاريخ الدفع"
    )
    notes = models.TextField(null=True, blank=True, verbose_name="ملاحظات")

    created_at = models.DateTimeField(auto_now_add=True)

    def _apply_delta(self, delta):
        """Applique une variation au montant payé du contrat (verrou de ligne)."""
        if not delta:
            return
        contract = Contract.objects.select_for_update().get(pk=self.contract_id)
        contract.montant_paye += delta
        contract.reste_a_payer = contract.montant_total - contract.montant_paye
        contract.save()
        self.contract = contract

    def save(self, *args, **kwargs):
        # Création/modification : le contrat suit la variation du montant (atomique).
        with transaction.atomic():
            old_amount = 0
            if self.pk is not None:
                old_amount = (
                    Payment.objects.filter(pk=self.pk)
                    .values_list("amount", flat=True)
                    .first()
                    or 0
                )
            super(Payment, self).save(*args, **kwargs)
            self._apply_delta(self.amount - old_amount)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            amount = self.amount
            result = super(Payment, self).delete(*args, **kwargs)
            self._apply_delta(-amount)
            return result

    def __str__(self):
        return f"Payment {self.id} - {self.contract} - {self.amount} MAD"


# ---------------------------------------------------------------------------
# Facturation : factures et avoirs (numérotation séquentielle sans trou)
# ---------------------------------------------------------------------------
class DocumentSequence(models.Model):
    """Compteur par agence, par année et par type de document (INV / CN)."""

    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=3)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "kind", "year"], name="docseq_agency_kind_year_uniq"
            )
        ]

    @classmethod
    def next_number(cls, agency, kind, year):
        """Prochain numéro (verrou de ligne : pas de doublon, pas de trou)."""
        from django.db import transaction

        with transaction.atomic():
            cls.objects.get_or_create(agency=agency, kind=kind, year=year)
            seq = cls.objects.select_for_update().get(
                agency=agency, kind=kind, year=year
            )
            seq.last_number += 1
            seq.save(update_fields=["last_number"])
            prefix = "F" if kind == "INV" else "AV"
            return f"{prefix}-{year}-{seq.last_number:04d}"


class Invoice(models.Model):
    STATUS_CHOICES = [
        ("EMISE", "Émise"),
        ("ANNULEE", "Annulée (avoir total)"),
    ]

    agency = models.ForeignKey(
        Agency, on_delete=models.PROTECT, related_name="invoices"
    )
    contract = models.ForeignKey(
        Contract, on_delete=models.PROTECT, related_name="invoices"
    )
    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="invoices"
    )
    number = models.CharField(max_length=30)
    date_emission = models.DateField(default=timezone.localdate)
    # Les montants du contrat sont TTC : HT et TVA en sont déduits au taux de l'agence.
    montant_ht = models.DecimalField(max_digits=12, decimal_places=2)
    tva_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    montant_tva = models.DecimalField(max_digits=12, decimal_places=2)
    montant_ttc = models.DecimalField(max_digits=12, decimal_places=2)
    statut = models.CharField(max_length=10, choices=STATUS_CHOICES, default="EMISE")
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_emission", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "number"], name="invoice_agency_number_uniq"
            )
        ]
        indexes = [
            models.Index(
                fields=["agency", "date_emission"], name="invoice_agency_date_idx"
            )
        ]

    def __str__(self):
        return self.number


class CreditNote(models.Model):
    agency = models.ForeignKey(
        Agency, on_delete=models.PROTECT, related_name="credit_notes"
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="credit_notes"
    )
    number = models.CharField(max_length=30)
    date_emission = models.DateField(default=timezone.localdate)
    montant_ht = models.DecimalField(max_digits=12, decimal_places=2)
    montant_tva = models.DecimalField(max_digits=12, decimal_places=2)
    montant_ttc = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_emission", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "number"], name="creditnote_agency_number_uniq"
            )
        ]

    def __str__(self):
        return self.number
