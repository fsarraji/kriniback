from django.contrib import admin
from .models import Contract, BookingRequest, Reservation

# 1. تسجيل جدول الوكالات
admin.site.register(Contract)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ['id', 'client', 'vehicle', 'statut', 'date_sortie', 'date_retour_prevue', 'created_at']
    list_filter = ['statut', 'agency', 'created_at']
    search_fields = ['client__nom', 'client__prenom', 'vehicle__matricule']


@admin.register(BookingRequest)
class BookingRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'nom', 'prenom', 'telephone', 'vehicle', 'statut', 'date_sortie', 'date_retour_prevue', 'created_at']
    list_filter = ['statut', 'agency', 'created_at']
    search_fields = ['nom', 'prenom', 'telephone', 'email']
