from django.contrib import admin

from .models import Infraction


@admin.register(Infraction)
class InfractionAdmin(admin.ModelAdmin):
    list_display = ("reference_pv", "matricule", "date_infraction", "status", "agency")
    list_filter = ("status", "agency")
    search_fields = ("reference_pv", "matricule")
