from django.contrib import admin
from .models import Vehicle, VehiclePriceInterval


class VehiclePriceIntervalInline(admin.TabularInline):
    model = VehiclePriceInterval
    extra = 0


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('matricule', 'matricule_definitif', 'marque', 'modele', 'annee', 'prix_par_jour', 'statut')
    search_fields = ('matricule', 'matricule_definitif', 'marque__name', 'modele__name')
    list_filter = ('statut', 'carburant', 'is_archived', 'is_deleted')
    readonly_fields = ('matricule_actuel',)
    fieldsets = (
        (None, {
            'fields': ('agency', 'matricule', 'matricule_definitif', 'matricule_actuel',
                       'marque', 'modele', 'annee', 'couleur', 'carburant', 'kilometrage', 'image'),
        }),
        ('Immatriculation & circulation', {
            'fields': ('date_mise_en_circulation', 'date_autorisation_circulation', 'puissance_fiscale'),
        }),
        ('Tarification', {
            'fields': ('prix_par_jour', 'tarif_km_extra'),
        }),
        ('Statut & opérations', {
            'fields': ('chauffeur_disponible', 'statut',
                       'date_assurance', 'date_visite_technique', 'prochain_vidange_km'),
        }),
        ('GPS', {
            'fields': ('gps_imei', 'sim_number', 'sim_operator'),
        }),
        ('Archivage', {
            'fields': ('is_archived', 'date_fin_travail', 'is_deleted'),
        }),
    )
    inlines = [VehiclePriceIntervalInline]
