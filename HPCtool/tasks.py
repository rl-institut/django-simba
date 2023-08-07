from celery import shared_task


@shared_task(bind=True)
def calculate_chargers(polygon):
    return "Hello World"
