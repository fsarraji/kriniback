from django.contrib import admin
from .models import Payment

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'contract', 'agency', 'amount', 'payment_method', 'payment_date', 'user')
    list_filter = ('agency', 'payment_method', 'payment_date')
    search_fields = ('contract__id', 'reference')
    date_hierarchy = 'payment_date'
