from django.db import models
from simple_history.models import HistoricalRecords

from agency.models import Agency, CustomUser  # استدعاء نموذج الوكالة
from car_rental_backend.uploads import (
    client_document_upload_cin,
    client_document_upload_permis,
    client_photo_upload,
)
from car_rental_backend.validators import validate_image_size


class Client(models.Model):
    SEXE_CHOICES = (
        ("HOMME", "Homme"),
        ("FEMME", "Femme"),
    )

    agency = models.ForeignKey(
        Agency,
        on_delete=models.CASCADE,
        related_name="clients",
        null=True,
        blank=True,
        verbose_name="الوكالة",
    )
    cin_passport = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        verbose_name="رقم البطاقة الوطنية أو الجواز",
    )
    date_expiration_cin = models.DateField(
        null=True, blank=True, verbose_name="تاريخ انتهاء صلاحية الوثيقة"
    )
    nationalite = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="الجنسية"
    )
    sexe = models.CharField(
        max_length=10, choices=SEXE_CHOICES, blank=True, null=True, verbose_name="Sexe"
    )
    nom = models.CharField(max_length=50, verbose_name="الاسم العائلي")
    prenom = models.CharField(max_length=50, verbose_name="الاسم الشخصي")
    telephone = models.CharField(max_length=20, verbose_name="رقم الهاتف")
    email = models.EmailField(blank=True, null=True, verbose_name="البريد الإلكتروني")
    permis_conduite = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="رقم رخصة السياقة",
    )
    date_delivrance_permis = models.DateField(
        verbose_name="تاريخ تسليم رخصة السياقة", null=True, blank=True
    )
    adresse = models.TextField(null=True, blank=True, verbose_name="العنوان")
    ville = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Ville"
    )
    pays = models.CharField(max_length=100, blank=True, null=True, verbose_name="Pays")

    # إضافات حديثة
    liste_noire = models.BooleanField(default=False, verbose_name="في القائمة السوداء؟")
    remarques = models.TextField(
        blank=True, null=True, verbose_name="ملاحظات حول الزبون"
    )

    # مستندات مرفقة (Scans/Images)
    photo = models.ImageField(
        upload_to=client_photo_upload,
        null=True,
        blank=True,
        verbose_name="صورة الملف الشخصي",
        validators=[validate_image_size],
    )
    scan_cin = models.ImageField(
        upload_to=client_document_upload_cin,
        null=True,
        blank=True,
        verbose_name="صورة البطاقة الوطنية/الجواز",
        validators=[validate_image_size],
    )
    scan_permis = models.ImageField(
        upload_to=client_document_upload_permis,
        null=True,
        blank=True,
        verbose_name="صورة رخصة السياقة",
        validators=[validate_image_size],
    )

    # حساب client (pour l'inscription et la connexion en ligne)
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="client_profile",
        verbose_name="Compte utilisateur",
    )

    # Suppression douce (masqué de l'annuaire, restauré par le super admin)
    is_deleted = models.BooleanField(default=False, verbose_name="Supprimé")

    # Journal d'audit : qui a modifié quoi, et quand
    history = HistoricalRecords()

    class Meta:
        # لضمان عدم تكرار رقم البطاقة الوطنية داخل *نفس الوكالة* فقط
        unique_together = ["agency", "cin_passport"]
        # Permis de conduire : unique au sein d'une même agence uniquement
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "permis_conduite"],
                condition=models.Q(permis_conduite__isnull=False)
                & ~models.Q(permis_conduite=""),
                name="uniq_client_permis_per_agency",
            )
        ]

    def __str__(self):
        return f"{self.nom} {self.prenom} - {self.cin_passport}"
