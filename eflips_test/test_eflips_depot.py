import eflips.depot.api.basic
import eflips.depot.api.output



import pytest

from eflips.depot.evaluation import DepotEvaluation
from eflips.depot.api.output import InputForSimba
import os

class TestEflipsSimbaOutput:

    @pytest.fixture
    def depot_evaluation(self):
        """his method creates a sample depot evaluation object containing some sample data. Since the depot evaluation
        is created at the end of the simulation, we need to create a simulation host object first.
        """
        absolute_path = os.path.dirname(__file__)

        # File configuration
        filename_eflips_settings = os.path.join(absolute_path, 'sample_simulation', 'settings')
        filename_schedule = os.path.join(absolute_path, 'sample_simulation', 'schedule')
        filename_template = os.path.join(absolute_path, 'sample_simulation', 'sample_depot')

        # Setup Simulation Host
        host = eflips.depot.api.basic.init_simulation(filename_eflips_settings, filename_schedule,
                                                  filename_template)

        # Run simulation
        ev = eflips.depot.api.basic.run_simulation(host)
        return ev

    def test_eflips_output(self, depot_evaluation: DepotEvaluation):

        # Generate input data for simBA

        assert isinstance(depot_evaluation, DepotEvaluation)
        data_for_simba = eflips.depot.api.basic.to_simba(depot_evaluation)
        assert data_for_simba is not None
        for i in data_for_simba:
            assert i is not None
            assert isinstance(i, InputForSimba)

            assert i.rotation_id is not None
            assert isinstance(i.rotation_id, int)

            assert i.vehicle_id is not None
            assert isinstance(i.vehicle_id, int) or isinstance(i.vehicle_id, str)

            assert i.soc_departure is not None
            assert isinstance(i.soc_departure, float)
            assert 0 <= i.soc_departure <= 1