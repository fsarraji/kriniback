from django.contrib import admin
from .models import Expense

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('title', 'agency', 'category', 'amount', 'expense_date', 'vehicle')
    list_filter = ('agency', 'category', 'expense_date')
    search_fields = ('title', 'notes')
    date_hierarchy = 'expense_date'
