from celery import shared_task

from .HPCChargerCalc import calculate_HPC

@shared_task(bind=True)
def calculate_chargers(self, polygon):

    print("SELF ", self)

    print("CELERY_GETS: ", polygon)
    aa = calculate_HPC(polygon)

    return aa
