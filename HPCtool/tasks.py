from celery import shared_task

from .HPCChargerCalc import calculate_HPC

@shared_task(bind=True)
def calculate_chargers(self, polygon, buslength, parkingdistance):
    #print("SELF ", self)
    print("CELERY_GETS: ", buslength, parkingdistance)
    return calculate_HPC(polygon, buslength, parkingdistance)
