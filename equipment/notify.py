"""시연용 대여 알림 메일.

환자는 MRN만 저장하므로 환자 연락처가 없다. 시연에서 "대여하면 메일이 간다"를 보여주려고,
DEMO_NOTIFY_MRN 환자에게 대여될 때만 DEMO_NOTIFY_EMAIL 로 보낸다. 실제 연동 시에는 Synergy 조회로 대체한다.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def notify_loan(loan):
    """대여가 DEMO_NOTIFY_MRN 환자에게 갔으면 메일을 보내고 True. 메일 문제로 대여가 취소되면 안 되므로 예외는 삼킨다."""
    recipient = settings.DEMO_NOTIFY_EMAIL
    mrn = str(loan.patient) if loan and loan.patient else ""
    if not recipient or mrn.upper() != settings.DEMO_NOTIFY_MRN.upper():
        return False
    try:
        send_mail(
            subject=f"[OT Tracker] {loan.equipment.asset_number} loaned to MRN {mrn}",
            message=(
                f"{loan.equipment} was loaned to MRN {mrn}.\n"
                f"Due back: {loan.due_at:%d %B %Y}.\n"
                f"Ward: {loan.ward or 'not recorded'}.\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Loan notice for %s could not be sent", loan.equipment.asset_number)
        return False
    return True
