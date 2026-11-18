"""Journal d'audit : historique des modifications (qui, quand, quoi) d'un objet."""
from rest_framework.response import Response
from rest_framework.views import APIView

from car_rental_backend.permissions import IsAgencyStaff
from clients.models import Client
from contracts.models import Contract
from expenses.models import Expense
from fleet.models import Vehicle
from payments.models import Payment

AUDITED_MODELS = {
    "contract": Contract,
    "payment": Payment,
    "vehicle": Vehicle,
    "client": Client,
    "expense": Expense,
}
HISTORY_TYPES = {"+": "created", "~": "updated", "-": "deleted"}
MAX_ENTRIES = 200
MAX_VALUE_LEN = 200


def _short(value):
    text = "" if value is None else str(value)
    return text if len(text) <= MAX_VALUE_LEN else text[: MAX_VALUE_LEN - 1] + "…"


class AuditLogView(APIView):
    """GET /api/audit/?model=contract&id=12 → historique de l'objet (personnel de l'agence)."""

    permission_classes = [IsAgencyStaff]

    def get(self, request):
        model = AUDITED_MODELS.get(request.query_params.get("model", ""))
        object_id = request.query_params.get("id", "")
        if model is None or not object_id.isdigit():
            return Response(
                {
                    "detail": "Paramètres requis : model ("
                    + ", ".join(AUDITED_MODELS)
                    + ") et id."
                },
                status=400,
            )

        records = model.history.filter(id=int(object_id))
        if not request.user.is_superuser:
            # Cloisonnement : on ne montre que l'historique des objets de son agence.
            records = records.filter(agency_id=request.user.agency_id)
        records = list(
            records.select_related("history_user").order_by(
                "-history_date", "-history_id"
            )[:MAX_ENTRIES]
        )

        entries = []
        for record in records:
            changes = []
            previous = record.prev_record
            if record.history_type == "~" and previous is not None:
                delta = record.diff_against(previous)
                changes = [
                    {"field": c.field, "old": _short(c.old), "new": _short(c.new)}
                    for c in delta.changes
                ]
            entries.append(
                {
                    "id": record.history_id,
                    "date": record.history_date.isoformat(),
                    "type": HISTORY_TYPES.get(record.history_type, record.history_type),
                    "user": record.history_user.get_username()
                    if record.history_user
                    else None,
                    "changes": changes,
                }
            )
        return Response(entries)
