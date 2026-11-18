from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from agency.models import Agency, CustomUser
from car_rental_backend.uploads import expense_receipt_upload_to
from car_rental_backend.validators import validate_image_size
from fleet.models import Vehicle


class Expense(models.Model):
    CATEGORY_CHOICES = [
        ("Maintenance", "صيانة سيارة"),
        ("Fuel", "وقود"),
        ("Salaries", "رواتب الموظفين"),
        ("Rent", "إيجار الوكالة"),
        ("Utilities", "كهرباء/ماء/إنترنت"),
        ("Taxes", "ضرائب وتأمين"),
        ("Other", "مصاريف أخرى"),
    ]

    agency = models.ForeignKey(
        Agency,
        on_delete=models.CASCADE,
        related_name="expenses",
        verbose_name="الوكالة",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name="السيارة (اختياري)",
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, verbose_name="الموظف المسجل"
    )

    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, verbose_name="تصنيف المصروف"
    )
    title = models.CharField(max_length=200, verbose_name="عنوان المصروف")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    expense_date = models.DateField(default=timezone.now, verbose_name="تاريخ المصروف")
    receipt_image = models.ImageField(
        upload_to=expense_receipt_upload_to,
        null=True,
        blank=True,
        verbose_name="صورة الوصل",
        validators=[validate_image_size],
    )
    notes = models.TextField(null=True, blank=True, verbose_name="ملاحظات")

    created_at = models.DateTimeField(auto_now_add=True)

    # Journal d'audit : qui a modifié quoi, et quand
    history = HistoricalRecords()

    class Meta:
        indexes = [
            models.Index(
                fields=["agency", "expense_date"], name="expense_agency_date_idx"
            ),
        ]

    def __str__(self):
        return f"{self.title} - {self.amount} MAD - {self.agency.nom_agence}"
