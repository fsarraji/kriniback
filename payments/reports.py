"""Rapports et analytique (agence-scopé), exportables en CSV (?export=csv).

Endpoints (/api/reports/…), période : ?from=YYYY-MM-DD&to=YYYY-MM-DD
(défaut : du 1er du mois à aujourd'hui) :

- occupancy/            taux d'occupation par véhicule et pour la flotte
- vehicle-profitability/ revenus encaissés − dépenses, par véhicule   (gérant)
- top-clients/          meilleurs clients (?limit=10)                  (gérant)
- revenue/              encaissements, dépenses et net par mois (?year=) (gérant)
- cash-journal/         journal de caisse d'un jour (?date=)

Les revenus sont en base « encaissements » (Payment.payment_date) ; les dépenses
en Expense.expense_date.
"""
import csv
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from car_rental_backend.permissions import IsAgencyStaff
from contracts.models import Contract
from expenses.models import Expense
from fleet.models import Vehicle

from .models import Payment

ZERO = Decimal("0")


class IsManager(permissions.BasePermission):
    """Gérant (OWNER) ou superuser."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and (u.is_superuser or u.role == "OWNER"))


class BadParam(Exception):
    pass


def _money(v):
    return str(Decimal(v or 0).quantize(Decimal("0.01")))


def _aware_start(d):
    return timezone.make_aware(datetime.combine(d, time.min))


def _aware_end_exclusive(d):
    return timezone.make_aware(datetime.combine(d + timedelta(days=1), time.min))


class ReportViewSet(viewsets.ViewSet):
    permission_classes = [IsAgencyStaff]

    def get_permissions(self):
        if self.action in ("vehicle_profitability", "top_clients", "revenue"):
            return [IsAgencyStaff(), IsManager()]
        return [IsAgencyStaff()]

    # ---- helpers ---------------------------------------------------------
    def _agency_filter(self, request):
        """Filtre agence : l'agence de l'utilisateur ; superuser : ?agency=<id> ou tout."""
        user = request.user
        if user.is_superuser:
            agency_id = request.query_params.get("agency")
            return {"agency_id": agency_id} if agency_id else {}
        return {"agency_id": user.agency_id}

    def _period(self, request):
        today = timezone.localdate()
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        try:
            start = parse_date(start) if start else today.replace(day=1)
            end = parse_date(end) if end else today
        except ValueError:
            raise BadParam("Dates invalides (YYYY-MM-DD).")
        if start is None or end is None:
            raise BadParam("Dates invalides (YYYY-MM-DD).")
        if end < start:
            raise BadParam("'to' doit être postérieur à 'from'.")
        if (end - start).days > 366:
            raise BadParam("Période maximale : 366 jours.")
        return start, end

    def _respond(self, request, data, rows, columns):
        if request.query_params.get("export") == "csv":
            resp = HttpResponse(content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = 'attachment; filename="rapport.csv"'
            resp.write("﻿")
            w = csv.writer(resp, delimiter=";")
            w.writerow(columns)
            for r in rows:
                w.writerow([r.get(c, "") for c in columns])
            return resp
        return Response(data)

    def _guard(self, fn, request):
        try:
            return fn(request)
        except BadParam as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    # ---- occupancy -------------------------------------------------------
    @action(detail=False, methods=["get"])
    def occupancy(self, request):
        return self._guard(self._occupancy, request)

    def _occupancy(self, request):
        start, end = self._period(request)
        p_start, p_end = _aware_start(start), _aware_end_exclusive(end)
        period_days = (end - start).days + 1
        af = self._agency_filter(request)

        vehicles = list(
            Vehicle.objects.filter(is_deleted=False, is_archived=False, **af)
            .select_related("marque", "modele")
            .order_by("matricule")
        )
        contracts = Contract.objects.filter(
            statut__in=["EN_COURS", "TERMINE"],
            date_sortie__lt=p_end,
            vehicle__in=[v.id for v in vehicles],
        ).values(
            "vehicle_id",
            "date_sortie",
            "date_retour_prevue",
            "date_retour_effective",
            "statut",
        )

        now = timezone.now()
        days_by_vehicle = defaultdict(float)
        rentals_by_vehicle = defaultdict(int)
        for c in contracts:
            c_end = c["date_retour_effective"] or c["date_retour_prevue"]
            if c["statut"] == "EN_COURS":
                c_end = (
                    max(c_end, now) if c_end < now else c_end
                )  # retard : toujours loué
            lo, hi = max(c["date_sortie"], p_start), min(c_end, p_end)
            if hi > lo:
                days_by_vehicle[c["vehicle_id"]] += (hi - lo).total_seconds() / 86400
                rentals_by_vehicle[c["vehicle_id"]] += 1

        rows = []
        for v in vehicles:
            d = min(days_by_vehicle[v.id], period_days)
            rows.append(
                {
                    "vehicle_id": v.id,
                    "matricule": v.matricule,
                    "vehicle": v.display_name,
                    "days_rented": round(d, 2),
                    "rentals": rentals_by_vehicle[v.id],
                    "occupancy_rate": round(100 * d / period_days, 1),
                }
            )
        fleet_rate = (
            round(sum(r["occupancy_rate"] for r in rows) / len(rows), 1) if rows else 0
        )
        data = {
            "from": start,
            "to": end,
            "period_days": period_days,
            "fleet_occupancy_rate": fleet_rate,
            "vehicles": rows,
        }
        return self._respond(
            request,
            data,
            rows,
            ["matricule", "vehicle", "rentals", "days_rented", "occupancy_rate"],
        )

    # ---- profitability ---------------------------------------------------
    @action(detail=False, methods=["get"], url_path="vehicle-profitability")
    def vehicle_profitability(self, request):
        return self._guard(self._profitability, request)

    def _profitability(self, request):
        start, end = self._period(request)
        p_start, p_end = _aware_start(start), _aware_end_exclusive(end)
        af = self._agency_filter(request)

        revenue = {
            r["contract__vehicle_id"]: r["t"] or ZERO
            for r in Payment.objects.filter(
                payment_date__gte=p_start,
                payment_date__lt=p_end,
                **{f"{k}": v for k, v in af.items()},
            )
            .values("contract__vehicle_id")
            .annotate(t=Sum("amount"))
        }
        costs = {
            r["vehicle_id"]: r["t"] or ZERO
            for r in Expense.objects.filter(
                expense_date__gte=start,
                expense_date__lte=end,
                vehicle__isnull=False,
                **af,
            )
            .values("vehicle_id")
            .annotate(t=Sum("amount"))
        }
        general_costs = (
            Expense.objects.filter(
                expense_date__gte=start,
                expense_date__lte=end,
                vehicle__isnull=True,
                **af,
            ).aggregate(t=Sum("amount"))["t"]
            or ZERO
        )
        vehicles = Vehicle.objects.filter(
            id__in=set(revenue) | set(costs), **af
        ).select_related("marque", "modele")
        rows = []
        for v in vehicles:
            rev, cost = revenue.get(v.id, ZERO), costs.get(v.id, ZERO)
            rows.append(
                {
                    "vehicle_id": v.id,
                    "matricule": v.matricule,
                    "vehicle": v.display_name,
                    "revenue": _money(rev),
                    "expenses": _money(cost),
                    "net": _money(rev - cost),
                }
            )
        rows.sort(key=lambda r: Decimal(r["net"]), reverse=True)
        total_rev = sum((Decimal(r["revenue"]) for r in rows), ZERO)
        total_cost = sum((Decimal(r["expenses"]) for r in rows), ZERO)
        data = {
            "from": start,
            "to": end,
            "vehicles": rows,
            "totals": {
                "revenue": _money(total_rev),
                "vehicle_expenses": _money(total_cost),
                "general_expenses": _money(
                    general_costs
                ),  # non rattachées à un véhicule
                "net_after_all_expenses": _money(
                    total_rev - total_cost - general_costs
                ),
            },
        }
        return self._respond(
            request, data, rows, ["matricule", "vehicle", "revenue", "expenses", "net"]
        )

    # ---- top clients -----------------------------------------------------
    @action(detail=False, methods=["get"], url_path="top-clients")
    def top_clients(self, request):
        return self._guard(self._top_clients, request)

    def _top_clients(self, request):
        start, end = self._period(request)
        try:
            limit = max(1, min(int(request.query_params.get("limit", 10)), 100))
        except ValueError:
            raise BadParam("limit invalide.")
        af = self._agency_filter(request)
        qs = (
            Contract.objects.filter(
                date_sortie__gte=_aware_start(start),
                date_sortie__lt=_aware_end_exclusive(end),
                **af,
            )
            .exclude(statut="ANNULE")
            .values("client_id", "client__nom", "client__prenom", "client__telephone")
            .annotate(
                contracts=Count("id"),
                total=Sum("montant_total"),
                paid=Sum("montant_paye"),
            )
            .order_by("-total")[:limit]
        )
        rows = [
            {
                "client_id": r["client_id"],
                "client": f'{r["client__prenom"]} {r["client__nom"]}',
                "telephone": r["client__telephone"],
                "contracts": r["contracts"],
                "total": _money(r["total"]),
                "paid": _money(r["paid"]),
                "unpaid": _money((r["total"] or ZERO) - (r["paid"] or ZERO)),
            }
            for r in qs
        ]
        data = {"from": start, "to": end, "clients": rows}
        return self._respond(
            request,
            data,
            rows,
            ["client", "telephone", "contracts", "total", "paid", "unpaid"],
        )

    # ---- revenue by month -----------------------------------------------
    @action(detail=False, methods=["get"])
    def revenue(self, request):
        return self._guard(self._revenue, request)

    def _revenue(self, request):
        try:
            year = int(request.query_params.get("year") or timezone.localdate().year)
        except ValueError:
            raise BadParam("year invalide.")
        af = self._agency_filter(request)
        pay = {
            timezone.localtime(r["m"]).month: r["t"] or ZERO
            for r in Payment.objects.filter(payment_date__year=year, **af)
            .annotate(m=TruncMonth("payment_date"))
            .values("m")
            .annotate(t=Sum("amount"))
        }
        exp = {
            r["m"].month: r["t"] or ZERO
            for r in Expense.objects.filter(expense_date__year=year, **af)
            .annotate(m=TruncMonth("expense_date"))
            .values("m")
            .annotate(t=Sum("amount"))
        }
        rows = []
        for month in range(1, 13):
            rev, cost = pay.get(month, ZERO), exp.get(month, ZERO)
            rows.append(
                {
                    "month": f"{year}-{month:02d}",
                    "revenue": _money(rev),
                    "expenses": _money(cost),
                    "net": _money(rev - cost),
                }
            )
        tr = sum((Decimal(r["revenue"]) for r in rows), ZERO)
        te = sum((Decimal(r["expenses"]) for r in rows), ZERO)
        data = {
            "year": year,
            "months": rows,
            "totals": {
                "revenue": _money(tr),
                "expenses": _money(te),
                "net": _money(tr - te),
            },
        }
        return self._respond(
            request, data, rows, ["month", "revenue", "expenses", "net"]
        )

    # ---- cash journal ----------------------------------------------------
    @action(detail=False, methods=["get"], url_path="cash-journal")
    def cash_journal(self, request):
        return self._guard(self._cash_journal, request)

    def _cash_journal(self, request):
        raw = request.query_params.get("date")
        day = parse_date(raw) if raw else timezone.localdate()
        if day is None:
            raise BadParam("date invalide (YYYY-MM-DD).")
        af = self._agency_filter(request)
        payments = list(
            Payment.objects.filter(
                payment_date__gte=_aware_start(day),
                payment_date__lt=_aware_end_exclusive(day),
                **af,
            )
            .select_related("contract__client", "contract__vehicle", "user")
            .order_by("payment_date")
        )
        by_method = defaultdict(lambda: ZERO)
        rows = []
        for p in payments:
            by_method[p.payment_method] += p.amount
            c = p.contract
            rows.append(
                {
                    "time": timezone.localtime(p.payment_date).strftime("%H:%M"),
                    "contract_id": c.id,
                    "client": f"{c.client.prenom} {c.client.nom}",
                    "vehicle": c.vehicle.matricule,
                    "method": p.payment_method,
                    "reference": p.reference or "",
                    "amount": _money(p.amount),
                    "received_by": p.user.username if p.user else "",
                }
            )
        expenses = (
            Expense.objects.filter(expense_date=day, **af).aggregate(t=Sum("amount"))[
                "t"
            ]
            or ZERO
        )
        total_in = sum(by_method.values(), ZERO)
        data = {
            "date": day,
            "payments": rows,
            "totals_by_method": {m: _money(t) for m, t in by_method.items()},
            "total_collected": _money(total_in),
            "total_expenses": _money(expenses),
            "cash_collected": _money(by_method.get("Espèce", ZERO)),
        }
        return self._respond(
            request,
            data,
            rows,
            [
                "time",
                "contract_id",
                "client",
                "vehicle",
                "method",
                "reference",
                "amount",
                "received_by",
            ],
        )
