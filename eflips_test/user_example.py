import os
import eflips.depot.api.basic
import eflips.depot.api.output

absolute_path = os.path.dirname(__file__)

# File configuration
filename_eflips_settings = os.path.join(absolute_path, 'simulation_files', 'kls_diss_settings_210219')
filename_schedule = os.path.join(absolute_path, 'simulation_files', 'schedule_kls_diss_scenario1_SB_DC_AB_OC_210203')
filename_template = os.path.join(absolute_path,
                                 'simulation_files', 'diss_kls_6xS, 94x150kW_SB, 147x75kW_AB, shunting+precond+chargeequationsteps')

# Setup Simulation Host
host = eflips.depot.api.basic.init_simulation(filename_eflips_settings, filename_schedule, filename_template)

# Run simulation
ev = eflips.depot.api.basic.run_simulation(host)

# Generate input data for simBA
data_for_simba = eflips.depot.api.basic.to_simba(ev)

# work with data from eFlips-Depot
# print(data_for_simba[0])
