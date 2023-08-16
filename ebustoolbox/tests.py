import time

from django.test import TestCase
# Create your tests here.
from .models import Scenario

class ScenarioTestCase(TestCase):
    def setUp(self):
        # Create test instances of your model
        Scenario.objects.create(name='Instance 1')
        time.sleep(0.01)
        Scenario.objects.create(name='Instance 2')

    def test_model_creation(self):
        instance_1 = Scenario.objects.get(name='Instance 1')
        instance_2 = Scenario.objects.get(name='Instance 2')
        self.assertGreater(instance_2.created, instance_1.created)
        self.assertIsNone(instance_1.finished)
        self.assertIsInstance(instance_1.options, dict)
        self.assertIsNone(instance_1.task_id)



