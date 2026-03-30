from django.db import models
from agency.models import Agency, CustomUser
from contracts.models import Contract
from django.utils import timezone

class Payment(models.Model):
    PAYMENT_METHODS = [
        ('Espèce', 'Espèce (Cash)'),
        ('Chèque', 'Chèque'),
        ('Virement', 'Virement (Bank Transfer)'),
        ('TPE', 'TPE (Card)'),
    ]

    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='payments', verbose_name="الوكالة")
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='payments_history', verbose_name="العقد")
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, verbose_name="الموظف المستلم")
    
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    payment_method = models.CharField(max_length=50, choices=PAYMENT_METHODS, default='Espèce', verbose_name="طريقة الدفع")
    reference = models.CharField(max_length=100, null=True, blank=True, verbose_name="المرجع (رقم الشيك أو التحويل)")
    payment_date = models.DateTimeField(default=timezone.now, verbose_name="تاريخ الدفع")
    notes = models.TextField(null=True, blank=True, verbose_name="ملاحظات")

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # When a payment is created, update the contract's amount paid
        is_new = self.pk is None
        super(Payment, self).save(*args, **kwargs)
        if is_new:
            self.contract.montant_paye += self.amount
            self.contract.reste_a_payer = self.contract.montant_total - self.contract.montant_paye
            self.contract.save()

    def __str__(self):
        return f"Payment {self.id} - {self.contract} - {self.amount} MAD"
