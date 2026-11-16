"""Factures et avoirs : règles métier, serializers, API, PDF et export CSV.

Règles :
- Les montants des contrats sont TTC ; HT et TVA sont déduits au taux de l'agence
  (Agency.tva_rate) figé sur la facture à l'émission.
- Une facture est immuable (pas de modification ni de suppression) : on corrige
  par un avoir. Numérotation séquentielle par agence/année/type, sans trou.
- Une seule facture « Émise » par contrat ; un avoir ne peut pas dépasser le
  reste facturé ; un avoir total passe la facture en « Annulée ».
"""
import csv
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.template.loader import get_template
from django.utils import timezone
from django.utils.dateparse import parse_date
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from car_rental_backend.permissions import IsAgencyStaff, check_same_agency
from contracts.models import Contract

from .models import CreditNote, DocumentSequence, Invoice

logger = logging.getLogger(__name__)
CENT = Decimal("0.01")


def _q(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def split_ttc(ttc, rate):
    """(HT, TVA, TTC) à partir d'un montant TTC et d'un taux en %."""
    ttc = _q(ttc)
    rate = Decimal(rate or 0)
    ht = _q(ttc / (1 + rate / Decimal(100)))
    return ht, _q(ttc - ht), ttc


def credited_total(invoice):
    return invoice.credit_notes.aggregate(t=Sum("montant_ttc"))["t"] or Decimal("0")


# --------------------------------------------------------------------------
# Création
# --------------------------------------------------------------------------
@transaction.atomic
def issue_invoice(contract, user, date_emission=None, notes=""):
    if contract.statut == "ANNULE":
        raise serializers.ValidationError(
            {"contract": "Contrat annulé : facturation impossible."}
        )
    if contract.montant_total is None or contract.montant_total <= 0:
        raise serializers.ValidationError(
            {"contract": "Le contrat n'a pas de montant à facturer."}
        )
    # Verrou sur le contrat : évite deux factures simultanées pour le même contrat.
    Contract.objects.select_for_update().get(pk=contract.pk)
    if Invoice.objects.filter(contract=contract, statut="EMISE").exists():
        raise serializers.ValidationError(
            {"contract": "Ce contrat a déjà une facture émise."}
        )

    agency = contract.agency
    rate = agency.tva_rate
    ht, tva, ttc = split_ttc(contract.montant_total, rate)
    extra = {"date_emission": date_emission} if date_emission else {}
    year = (date_emission or timezone.localdate()).year
    return Invoice.objects.create(
        agency=agency,
        contract=contract,
        client=contract.client,
        number=DocumentSequence.next_number(agency, "INV", year),
        montant_ht=ht,
        tva_rate=rate,
        montant_tva=tva,
        montant_ttc=ttc,
        notes=notes or "",
        created_by=user,
        **extra,
    )


@transaction.atomic
def issue_credit_note(invoice, amount_ttc, reason, user):
    # Verrou sur la facture : sérialise les avoirs concurrents.
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    remaining = invoice.montant_ttc - credited_total(invoice)
    amount = _q(amount_ttc if amount_ttc is not None else remaining)
    if amount <= 0:
        raise serializers.ValidationError(
            {"amount": "Le montant de l'avoir doit être positif."}
        )
    if amount > remaining:
        raise serializers.ValidationError(
            {"amount": f"L'avoir dépasse le reste facturé ({remaining})."}
        )
    ht, tva, ttc = split_ttc(amount, invoice.tva_rate)
    today = timezone.localdate()
    note = CreditNote.objects.create(
        agency=invoice.agency,
        invoice=invoice,
        number=DocumentSequence.next_number(invoice.agency, "CN", today.year),
        date_emission=today,
        montant_ht=ht,
        montant_tva=tva,
        montant_ttc=ttc,
        reason=reason or "",
        created_by=user,
    )
    if credited_total(invoice) >= invoice.montant_ttc:
        invoice.statut = "ANNULEE"
        invoice.save(update_fields=["statut"])
    return note


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------
class CreditNoteSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)

    class Meta:
        model = CreditNote
        fields = [
            "id",
            "number",
            "invoice",
            "invoice_number",
            "date_emission",
            "montant_ht",
            "montant_tva",
            "montant_ttc",
            "reason",
            "created_at",
        ]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    client_name = serializers.SerializerMethodField()
    montant_credite = serializers.SerializerMethodField()
    montant_paye = serializers.DecimalField(
        source="contract.montant_paye", max_digits=12, decimal_places=2, read_only=True
    )
    credit_notes = CreditNoteSerializer(many=True, read_only=True)
    # Entrées (écriture) : contrat à facturer
    contract = serializers.PrimaryKeyRelatedField(queryset=Contract.objects.all())
    date_emission = serializers.DateField(required=False)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "contract",
            "client",
            "client_name",
            "date_emission",
            "montant_ht",
            "tva_rate",
            "montant_tva",
            "montant_ttc",
            "statut",
            "montant_paye",
            "montant_credite",
            "notes",
            "credit_notes",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "number",
            "client",
            "montant_ht",
            "tva_rate",
            "montant_tva",
            "montant_ttc",
            "statut",
            "created_at",
        ]

    def get_client_name(self, obj):
        return f"{obj.client.prenom} {obj.client.nom}"

    def get_montant_credite(self, obj):
        return str(sum((n.montant_ttc for n in obj.credit_notes.all()), Decimal("0")))

    def validate_contract(self, contract):
        check_same_agency(self.context.get("request"), contract, "contract")
        return contract


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def _render_pdf(context):
    from contracts.views import generate_pdf

    html = get_template("contracts/invoice_pdf.html").render(context)
    return generate_pdf(html)


def _pdf_response(pdf, filename):
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


# --------------------------------------------------------------------------
# ViewSets
# --------------------------------------------------------------------------
class InvoiceViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = InvoiceSerializer
    permission_classes = [IsAgencyStaff]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["statut", "contract", "client"]
    search_fields = ["number", "client__nom", "client__prenom"]
    ordering_fields = ["date_emission", "montant_ttc", "id"]

    def get_queryset(self):
        qs = Invoice.objects.select_related("client", "contract").prefetch_related(
            "credit_notes"
        )
        user = self.request.user
        qs = qs if user.is_superuser else qs.filter(agency=user.agency)
        # Filtre de période (date d'émission) : ?from=YYYY-MM-DD&to=YYYY-MM-DD
        start = parse_date(self.request.query_params.get("from") or "")
        end = parse_date(self.request.query_params.get("to") or "")
        if start:
            qs = qs.filter(date_emission__gte=start)
        if end:
            qs = qs.filter(date_emission__lte=end)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = issue_invoice(
            serializer.validated_data["contract"],
            request.user,
            date_emission=serializer.validated_data.get("date_emission"),
            notes=serializer.validated_data.get("notes", ""),
        )
        return Response(
            self.get_serializer(invoice).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="credit-note")
    def credit_note(self, request, pk=None):
        """Émet un avoir (total par défaut, ou partiel avec `amount`). Gérant uniquement."""
        if not (request.user.is_superuser or request.user.role == "OWNER"):
            return Response(
                {"detail": "Seul le gérant peut émettre un avoir."},
                status=status.HTTP_403_FORBIDDEN,
            )
        invoice = self.get_object()
        amount = request.data.get("amount")
        try:
            amount = Decimal(str(amount)) if amount not in (None, "") else None
        except Exception:
            raise serializers.ValidationError({"amount": "Montant invalide."})
        note = issue_credit_note(
            invoice, amount, request.data.get("reason", ""), request.user
        )
        return Response(CreditNoteSerializer(note).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        invoice = self.get_object()
        try:
            pdf = _render_pdf(
                {
                    "doc_type": "FACTURE",
                    "number": invoice.number,
                    "date": invoice.date_emission,
                    "agency": invoice.agency,
                    "client": invoice.client,
                    "contract": invoice.contract,
                    "ht": invoice.montant_ht,
                    "tva_rate": invoice.tva_rate,
                    "tva": invoice.montant_tva,
                    "ttc": invoice.montant_ttc,
                    "paid": invoice.contract.montant_paye,
                    "reste": invoice.montant_ttc - invoice.contract.montant_paye,
                    "invoice": invoice,
                    "reason": invoice.notes,
                }
            )
        except Exception:
            logger.exception("invoice_pdf_failed", extra={"invoice_id": invoice.id})
            return Response(
                {"detail": "Erreur lors de la génération du PDF."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return _pdf_response(pdf, f"{invoice.number}.pdf")

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Export CSV comptable (factures + avoirs en négatif) sur la période filtrée."""
        invoices = self.filter_queryset(self.get_queryset())
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="factures.csv"'
        resp.write("﻿")  # BOM : ouverture correcte dans Excel
        w = csv.writer(resp, delimiter=";")
        w.writerow(
            [
                "Type",
                "Numéro",
                "Date",
                "Client",
                "Contrat",
                "Facture liée",
                "HT",
                "TVA",
                "TTC",
                "Statut",
            ]
        )
        for inv in invoices:
            w.writerow(
                [
                    "Facture",
                    inv.number,
                    inv.date_emission,
                    f"{inv.client.prenom} {inv.client.nom}",
                    inv.contract_id,
                    "",
                    inv.montant_ht,
                    inv.montant_tva,
                    inv.montant_ttc,
                    inv.statut,
                ]
            )
            for cn in inv.credit_notes.all():
                w.writerow(
                    [
                        "Avoir",
                        cn.number,
                        cn.date_emission,
                        f"{inv.client.prenom} {inv.client.nom}",
                        inv.contract_id,
                        inv.number,
                        -cn.montant_ht,
                        -cn.montant_tva,
                        -cn.montant_ttc,
                        "",
                    ]
                )
        return resp


class CreditNoteViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = CreditNoteSerializer
    permission_classes = [IsAgencyStaff]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["invoice"]
    ordering_fields = ["date_emission", "id"]

    def get_queryset(self):
        qs = CreditNote.objects.select_related("invoice")
        user = self.request.user
        return qs if user.is_superuser else qs.filter(agency=user.agency)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        note = self.get_object()
        invoice = note.invoice
        try:
            pdf = _render_pdf(
                {
                    "doc_type": "AVOIR",
                    "number": note.number,
                    "date": note.date_emission,
                    "agency": note.agency,
                    "client": invoice.client,
                    "contract": invoice.contract,
                    "ht": note.montant_ht,
                    "tva_rate": invoice.tva_rate,
                    "tva": note.montant_tva,
                    "ttc": note.montant_ttc,
                    "invoice": invoice,
                    "reason": note.reason,
                }
            )
        except Exception:
            logger.exception(
                "credit_note_pdf_failed", extra={"credit_note_id": note.id}
            )
            return Response(
                {"detail": "Erreur lors de la génération du PDF."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return _pdf_response(pdf, f"{note.number}.pdf")
