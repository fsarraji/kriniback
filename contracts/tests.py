from django.db import models
from django.test import TestCase
from agency.models import Agency, CustomUser
from fleet.models import Vehicle
from clients.models import Client
from django.utils import timezone

class Contract(models.Model):
    # حالات العقد
    STATUS_CHOICES =[
        ('RESERVE', 'محجوزة مسبقاً'),
        ('EN_COURS', 'في طور الكراء (جارية)'),
        ('TERMINE', 'منتهية (تم إرجاع السيارة)'),
        ('ANNULE', 'ملغاة'),
    ]

    # مستويات الوقود
    FUEL_LEVELS =[
        ('0', 'فارغ'),
        ('1/4', 'الربع'),
        ('1/2', 'النصف'),
        ('3/4', 'ثلاثة أرباع'),
        ('1', 'ممتلئ'),
    ]

    # 1. روابط الـ Multi-Agency والمستخدمين
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='contracts')
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, verbose_name="الموظف الذي أنشأ العقد")
    
    # 2. أطراف العقد (السيارة والزبون)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='contracts', verbose_name="السيارة")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='contracts', verbose_name="الزبون المكتري")
    deuxieme_chauffeur = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='contracts_as_second', verbose_name="السائق الثاني")

    # 3. التواريخ والمدة
    date_sortie = models.DateTimeField(default=timezone.now, verbose_name="تاريخ ووقت التسليم")
    date_retour_prevue = models.DateTimeField(verbose_name="تاريخ ووقت الإرجاع المتوقع")
    date_retour_effective = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الإرجاع الفعلي") # يتم ملؤه عند إرجاع السيارة
    jours = models.IntegerField(default=1, verbose_name="عدد الأيام")

    # 4. الحالة عند التسليم (Livraison)
    km_sortie = models.IntegerField(verbose_name="الكيلومتراج عند الخروج")
    carburant_sortie = models.CharField(max_length=10, choices=FUEL_LEVELS, default='1/4', verbose_name="الوقود عند الخروج")
    
    # 5. الحالة عند الإرجاع (Réception)
    km_retour = models.IntegerField(null=True, blank=True, verbose_name="الكيلومتراج عند الإرجاع")
    carburant_retour = models.CharField(max_length=10, choices=FUEL_LEVELS, null=True, blank=True, verbose_name="الوقود عند الإرجاع")
    degats_retour = models.TextField(null=True, blank=True, verbose_name="ملاحظات/أضرار عند الإرجاع")

    # 6. الحسابات المالية (Finance)
    prix_par_jour = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="ثمن الكراء اليومي المتفق عليه")
    montant_total = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ الإجمالي")
    montant_paye = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="المبلغ المدفوع (التسبيق)")
    reste_a_payer = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="المبلغ المتبقي")
    caution = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="مبلغ الضمانة (Caution)")
    methode_paiement = models.CharField(max_length=50, default="Espèce", verbose_name="طريقة الدفع") # Espèce, Chèque, TPE, Virement

    # 7. حالة العقد
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='EN_COURS', verbose_name="حالة العقد")
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'contracts_tests'

    def save(self, *args, **kwargs):
        # 1. حساب المبلغ الإجمالي والمتبقي تلقائياً قبل حفظ العقد
        if self.prix_par_jour and self.jours:
            self.montant_total = self.prix_par_jour * self.jours
            self.reste_a_payer = self.montant_total - self.montant_paye
            
        # 2. تحديث الكيلومتراج الخاص بالسيارة إذا تم إرجاعها
        if self.statut == 'TERMINE' and self.km_retour:
            self.vehicle.kilometrage = self.km_retour
            self.vehicle.statut = 'Available'
            self.vehicle.save()
            
        # 3. تحديث حالة السيارة إذا كانت في طور الكراء
        elif self.statut == 'EN_COURS':
            self.vehicle.statut = 'Rented'
            self.vehicle.save()

        super(Contract, self).save(*args, **kwargs)

    def __str__(self):
        return f"عقد رقم {self.id} - {self.client.nom} - {self.vehicle.matricule}"


class NaiveDatetimeNormalizationTests(TestCase):
    """Les datetimes naïfs envoyés par les formulaires agence (datetime-local)
    sont rendus timezone-aware à l'enregistrement (plus de RuntimeWarning)."""

    def setUp(self):
        from datetime import date
        from decimal import Decimal
        from agency.models import Agency
        from fleet.models import Brand, ModelCar, Vehicle

        self.agency = Agency.objects.create(nom_agence='Agence Test', adresse='x', telephone='0612345678')
        brand = Brand.objects.create(name='Dacia')
        modele = ModelCar.objects.create(brand=brand, name='Sandero')
        self.vehicle = Vehicle.objects.create(
            agency=self.agency, matricule='TEST-001', marque=brand, modele=modele,
            annee=2020, couleur='Blanc', carburant='Diesel', kilometrage=1000,
            prix_par_jour=Decimal('250.00'), date_assurance=date(2026, 1, 1),
            date_visite_technique=date(2026, 1, 1), prochain_vidange_km=50000,
        )

    def test_reservation_serializer_normalise(self):
        from datetime import datetime, timezone as dt_tz
        from contracts.serializers import ReservationSerializer
        s = ReservationSerializer(data={
            'vehicle': self.vehicle.id,
            'date_sortie': '2026-09-14T21:50',
            'date_retour_prevue': '2026-09-19T22:50',
        })
        self.assertTrue(s.is_valid(), s.errors)
        self.assertTrue(timezone.is_aware(s.validated_data['date_sortie']))
        self.assertTrue(timezone.is_aware(s.validated_data['date_retour_prevue']))
        self.assertEqual(s.validated_data['date_sortie'], datetime(2026, 9, 14, 21, 50, tzinfo=dt_tz.utc))

    def test_contract_serializer_normalise(self):
        from datetime import datetime, timezone as dt_tz
        from contracts.serializers import ContractSerializer
        s = ContractSerializer(data={
            'vehicle': self.vehicle.id,
            'date_sortie': '2026-09-14T21:50',
            'date_retour_prevue': '2026-09-19T22:50',
        }, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        self.assertTrue(timezone.is_aware(s.validated_data['date_sortie']))
        self.assertTrue(timezone.is_aware(s.validated_data['date_retour_prevue']))
        self.assertEqual(s.validated_data['date_sortie'], datetime(2026, 9, 14, 21, 50, tzinfo=dt_tz.utc))

    def test_reservation_api_stores_aware_datetime(self):
        from agency.models import CustomUser
        from clients.models import Client
        from contracts.models import Reservation
        from rest_framework.test import APIClient

        user = CustomUser.objects.create_user(
            username='testclient', password='pass12345', role='CLIENT', agency=self.agency,
        )
        Client.objects.create(agency=self.agency, user=user, nom='Alaoui', prenom='Ahmed', telephone='0612345678')

        c = APIClient()
        c.force_authenticate(user=user)
        res = c.post('/api/reservations/', {
            'vehicle': self.vehicle.id,
            'date_sortie': '2026-09-14T21:50',
            'date_retour_prevue': '2026-09-19T22:50',
        }, format='json')

        self.assertEqual(res.status_code, 201, res.data)
        reservation = Reservation.objects.get(pk=res.data['id'])
        self.assertTrue(timezone.is_aware(reservation.date_sortie))
        self.assertTrue(timezone.is_aware(reservation.date_retour_prevue))