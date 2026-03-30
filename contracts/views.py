from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
import os
import tempfile
import base64
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import math
from io import BytesIO
from .models import Contract, ContractDamage
from .serializers import ContractSerializer

def generate_fuel_gauge_image(level_str):
    if not level_str:
        return None
    try:
        level = int(level_str.split('/')[0])
    except:
        level = 0
    levels = 8
    
    width, height = 200, 120
    img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    cx, cy = 100, 100
    r = 80
    
    draw.arc([cx - r, cy - r, cx + r, cy + r], 180, 0, fill='#e2e8f0', width=3)
    
    try:
        font = ImageFont.truetype("arial.ttf", size=20)
        font_small = ImageFont.truetype("arial.ttf", size=16)
    except IOError:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
        
    draw.text((30, 115), "E", fill='#0f172a', font=font, anchor="mm")
    draw.text((170, 115), "F", fill='#0f172a', font=font, anchor="mm")
    
    for i in range(levels + 1):
        a = 180 - (i / levels) * 180
        rad = math.radians(a)
        is_major = i % 2 == 0
        length = 14 if is_major else 7
        r_in = r - length
        r_out = r
        x1 = cx + r_in * math.cos(rad)
        y1 = cy - r_in * math.sin(rad)
        x2 = cx + r_out * math.cos(rad)
        y2 = cy - r_out * math.sin(rad)
        
        color = '#ef4444' if i <= 1 else '#0f172a'
        line_width = 4 if is_major else 2
        draw.line((x1, y1, x2, y2), fill=color, width=line_width)

    needle_angle = 180 - (level / levels) * 180
    needle_rad = math.radians(needle_angle)
    needle_length = r - 18
    nx = cx + needle_length * math.cos(needle_rad)
    ny = cy - needle_length * math.sin(needle_rad)
    
    tail_length = 12
    tx = cx - tail_length * math.cos(needle_rad)
    ty = cy + tail_length * math.sin(needle_rad)
    
    draw.line((tx, ty, nx, ny), fill='#ef4444', width=5)
    
    dot_r = 8
    draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill='#0f172a')
    draw.text((cx, cy - 25), f"{level}/8", fill='#64748b', font=font_small, anchor="mm")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{img_str}"

class ContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Contract.objects.all().order_by('-date_creation')
        return Contract.objects.filter(agency=self.request.user.agency).order_by('-date_creation')

    def perform_create(self, serializer):
        serializer.save(agency=self.request.user.agency, created_by=self.request.user)

    # مسار مخصص لإغلاق العقد وإرجاع السيارة
    @action(detail=True, methods=['post'])
    def return_vehicle(self, request, pk=None):
        contract = self.get_object()

        # التأكد من أن العقد لم ينتهِ بالفعل
        if contract.statut == 'TERMINE':
            return Response({'detail': 'هذا العقد منتهي بالفعل.'}, status=status.HTTP_400_BAD_REQUEST)
        if contract.statut == 'ANNULE':
            return Response({'detail': 'لا يمكن إغلاق عقد ملغى.'}, status=status.HTTP_400_BAD_REQUEST)

        # --- البيانات الأساسية ---
        km_retour = request.data.get('km_retour')
        carburant_retour = request.data.get('carburant_retour')
        degats_retour = request.data.get('degats_retour', '')
        date_retour_effective = request.data.get('date_retour_effective')

        if not km_retour:
            return Response({'detail': 'يجب إدخال الكيلومتراج عند الإرجاع.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            km_retour_int = int(km_retour)
        except (ValueError, TypeError):
            return Response({'detail': 'الكيلومتراج يجب أن يكون رقماً صحيحاً.'}, status=status.HTTP_400_BAD_REQUEST)

        if km_retour_int < contract.km_sortie:
            return Response(
                {'detail': f'كيلومتراج الإرجاع ({km_retour_int}) لا يمكن أن يكون أقل من كيلومتراج الخروج ({contract.km_sortie}).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # --- تحديث الحقول الأساسية ---
        contract.km_retour = km_retour_int
        contract.carburant_retour = carburant_retour
        contract.degats_retour = degats_retour

        if date_retour_effective:
            from django.utils.dateparse import parse_datetime
            parsed_date = parse_datetime(date_retour_effective)
            contract.date_retour_effective = parsed_date if parsed_date else timezone.now()
        else:
            contract.date_retour_effective = timezone.now()

        # --- Recalcul de la durée et du montant ---
        import math
        diff = contract.date_retour_effective - contract.date_sortie
        # Calcul des jours (tout jour entamé est dû, min 1 jour)
        new_jours = math.ceil(diff.total_seconds() / (24 * 3600))
        if new_jours < 1:
            new_jours = 1
        
        contract.jours = new_jours
        # Le save() mettra à jour montant_total et reste_a_payer
        contract.save()

        # --- Traitement du paiement (Règlement final) ---
        payment_amount = request.data.get('payment_amount', 0)
        payment_method = request.data.get('payment_method', 'Espèce')
        
        if payment_amount and float(payment_amount) > 0:
            from payments.models import Payment
            Payment.objects.create(
                agency=contract.agency,
                contract=contract,
                user=request.user,
                amount=payment_amount,
                payment_method=payment_method,
                notes="Règlement final lors de la clôture du contrat"
            )
            # Recharger pour avoir les montants à jour (montant_paye/reste_a_payer)
            contract.refresh_from_db()

        # --- ÉTAT DES ACCESSOIRES AU RETOUR ---
        accessories_retour = request.data.get('accessories_retour', {})
        if isinstance(accessories_retour, dict):
            contract.roue_secours_retour = accessories_retour.get('roue_secours', False)
            contract.cric_retour = accessories_retour.get('cric', False)
            contract.manivelle_retour = accessories_retour.get('manivelle', False)
            contract.gilet_retour = accessories_retour.get('gilet', False)
            contract.triangle_retour = accessories_retour.get('triangle', False)
            contract.extincteur_retour = accessories_retour.get('extincteur', False)
            contract.papiers_retour = accessories_retour.get('papiers', False)
            contract.cles_retour = accessories_retour.get('cles', False)

        # --- نقاط الأضرار عند الإرجاع ---
        damages_retour = request.data.get('damages_retour', [])
        if isinstance(damages_retour, list):
            # حذف أي أضرار سابقة من نوع RETOUR لهذا العقد
            contract.damages.filter(type='RETOUR').delete()
            for dmg in damages_retour:
                try:
                    ContractDamage.objects.create(
                        contract=contract,
                        type='RETOUR',
                        x=float(dmg.get('x', 0)),
                        y=float(dmg.get('y', 0)),
                        description=dmg.get('description', '')
                    )
                except (ValueError, TypeError):
                    pass

        contract.statut = 'TERMINE'
        contract.save()

        return Response({
            'detail': 'تم إغلاق العقد وإرجاع السيارة بنجاح.',
            'reste_a_payer': str(contract.reste_a_payer),
            'date_retour_effective': contract.date_retour_effective.isoformat() if contract.date_retour_effective else None,
            'km_parcourus': contract.km_retour - contract.km_sortie,
        }, status=status.HTTP_200_OK)
        
    @action(detail=True, methods=['get'])
    def print_contract(self, request, pk=None):
        # 1. جلب العقد المطلوب
        contract = self.get_object()
        agency = request.user.agency

        # 2. تحديد مسار القالب (HTML Template)
        template_path = 'contracts/contract_pdf.html'
        
        car_diagram_path = r'd:\location\car_rental_frontend\src\assets\car_damage_diagram.png'
        depart_damages = contract.damages.filter(type='DEPART')
        retour_damages = contract.damages.filter(type='RETOUR')

        def build_diagram_base64(damages, dot_color='red'):
            """Draw damage markers on the car diagram and return base64 PNG."""
            if not os.path.exists(car_diagram_path):
                return ""
            try:
                img = Image.open(car_diagram_path).convert('RGBA')
                if damages.exists():
                    draw = ImageDraw.Draw(img)
                    w, h = img.size
                    try:
                        font = ImageFont.truetype("arial.ttf", size=int(w * 0.04))
                    except IOError:
                        font = ImageFont.load_default()
                    for i, dmg in enumerate(damages):
                        x_px = (dmg.x / 100.0) * w
                        y_px = (dmg.y / 100.0) * h
                        radius = w * 0.02
                        draw.ellipse((x_px - radius, y_px - radius, x_px + radius, y_px + radius), fill=dot_color)
                        draw.text((x_px, y_px), str(i + 1), fill='white', font=font, anchor="mm")
                buffered = BytesIO()
                img.save(buffered, format="PNG")
                encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
                return f"data:image/png;base64,{encoded}"
            except Exception as e:
                print("Error building diagram:", e)
                return ""

        car_diagram_base64 = build_diagram_base64(depart_damages, dot_color='red')
        retour_diagram_base64 = build_diagram_base64(retour_damages, dot_color='orange')
            
        # 3. البيانات التي سنرسلها للقالب
        fuel_depart_base64 = generate_fuel_gauge_image(contract.carburant_sortie)
        fuel_retour_base64 = generate_fuel_gauge_image(contract.carburant_retour)

        km_parcourus = contract.km_retour - contract.km_sortie if contract.km_retour else None

        context = {
            'contract': contract,
            'agency': agency,
            'car_diagram_base64': car_diagram_base64,
            'retour_diagram_base64': retour_diagram_base64,
            'depart_damages': depart_damages,
            'retour_damages': retour_damages,
            'fuel_depart_base64': fuel_depart_base64,
            'fuel_retour_base64': fuel_retour_base64,
            'km_parcourus': km_parcourus,
        }

        # 4. إعداد الـ HTTP Response ليكون من نوع PDF
        response = HttpResponse(content_type='application/pdf')
        # 'inline' تعني فتح الـ PDF في المتصفح مباشرة للطباعة. 
        # إذا أردت تحميله مباشرة استبدل 'inline' بـ 'attachment'
        response['Content-Disposition'] = f'inline; filename="Contrat_{contract.id}.pdf"'

        # 5. جلب القالب ورسمه (Render)
        template = get_template(template_path)
        html = template.render(context)

        # 6. تحويل الـ HTML إلى PDF باستخدام xhtml2pdf
        pisa_status = pisa.CreatePDF(html, dest=response)

        # التحقق من وجود أخطاء
        if pisa_status.err:
            return Response('عذراً، حدث خطأ أثناء توليد ملف الـ PDF', status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return response    