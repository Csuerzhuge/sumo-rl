import os
import sys
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare the environment variable 'SUMO_HOME'")
import traci
import sumolib
from gym import Env
import traci.constants as tc
from gym import spaces
from ray.rllib.env.multi_agent_env import MultiAgentEnv
import numpy as np
import pandas as pd

from .traffic_signal import TrafficSignal


class SumoEnvironment(MultiAgentEnv):

    def __init__(self, net_file, route_file, phases,
                 use_gui=False,
                 num_seconds=20000,
                 max_depart_delay=100000,
                 time_to_load_vehicles=0,
                 delta_time=5,
                 min_green=10,
                 max_green=50):

        self._net = net_file
        self._route = route_file
        if use_gui:
            self._sumo_binary = sumolib.checkBinary('sumo-gui')
        else:
            self._sumo_binary = sumolib.checkBinary('sumo')

        self.ts_ids = list()
        self.traffic_signals = dict()
        self.phases = phases
        self.vehicles = {}
        self.last_measure = dict()    # used to reward function remember last measure
        self.sim_max_time = num_seconds
        self.time_to_load_vehicles = time_to_load_vehicles  # number of seconds of simulation ran in reset()
        self.delta_time = delta_time  # seconds on sumo at each step
        self.max_depart_delay = max_depart_delay  # Max wait time to insert a vehicle
        self.min_green = min_green
        self.max_green = max_green
        self.yellow_time = 2

        self.observation_space = spaces.Box(low=np.zeros(12), high=np.ones(12))
        self.discrete_observation_space = spaces.Tuple((
            *(spaces.Discrete(2) for _ in range(len(phases)//2)),   # Green Phase
            spaces.Discrete(self.max_green//self.delta_time),  # Elapsed time of phase
            *(spaces.Discrete(10) for _ in range(len(phases)))    # Density and stopped-density for each green phase
        ))
        self.action_space = spaces.Discrete(len(phases) // 2)   # Number of green phases == number of phases (green+yellow) divided by 2

        self.radix_factors = [s.n for s in self.discrete_observation_space.spaces]
        self.run = 0
        self.metrics = []
        
    def reset(self):
        if self.run != 0:
            df = pd.DataFrame(self.metrics)
            df.to_csv('outputs/2way-single-intersection/a3cteste{}.csv'.format(self.run), index=False)
        self.run += 1
        self.metrics = []
        sumo_cmd = [self._sumo_binary,
                     '-n', self._net,
                     '-r', self._route,
                     '--max-depart-delay', str(self.max_depart_delay), 
                     '--waiting-time-memory', '10000', 
                     '--random']
        traci.start(sumo_cmd)

        self.ts_ids = traci.trafficlight.getIDList()
        for ts in self.ts_ids:
            self.traffic_signals[ts] = TrafficSignal(self, ts, self.delta_time, self.min_green, self.max_green, self.phases)
            self.last_measure[ts] = 0.0
        self.vehicles = {}

        # Load vehicles
        for _ in range(self.time_to_load_vehicles):
            self._sumo_step()

        return self._compute_observations()

    @property
    def sim_step(self):
        return traci.simulation.getCurrentTime()/1000  # milliseconds to seconds

    def step(self, actions):
        # act
        self.apply_actions(actions)
   
        # run simulation for delta time
        for _ in range(self.yellow_time): 
            self._sumo_step()
        for ts in self.ts_ids:
            self.traffic_signals[ts].update_phase()
        for _ in range(self.delta_time - self.yellow_time):
            self._sumo_step()

        # observe new state and reward
        observation = self._compute_observations()
        reward = self._compute_rewards()
        done = {'__all__': self.sim_step > self.sim_max_time}
        info = self._compute_step_info()
        self.metrics.append(info)
        return observation, reward, done, {}

    def apply_actions(self, actions):
        for ts, action in actions.items():
            self.traffic_signals[ts].set_next_phase(action)

    def _compute_observations(self):
        observations = {}
        for ts in self.ts_ids:
            phase_id = [1 if self.traffic_signals[ts].phase//2 == i else 0 for i in range(len(self.phases)//2)]  #one-hot encoding
            elapsed = self.traffic_signals[ts].time_on_phase / self.max_green
            density = self.traffic_signals[ts].get_density()
            stop_density = self.traffic_signals[ts].get_stopped_density()

            observations[ts] = phase_id + [elapsed] + density + stop_density
        return observations

    def _compute_rewards(self):
        return self._waiting_time_reward()
        #return self._waiting_time_reward2()
        #return self._queue_average_reward()

    def _queue_average_reward(self):
        rewards = {}
        for ts in self.ts_ids:
            new_average = np.mean(self.traffic_signals[ts].get_stopped_vehicles_num())
            rewards[ts] = self.last_measure[ts] - new_average
            self.last_measure[ts] = new_average
        return rewards

    def _waiting_time_reward(self):
        rewards = {}
        for ts in self.ts_ids:
            ts_wait = sum(self.traffic_signals[ts].get_waiting_time())
            rewards[ts] = self.last_measure[ts] - ts_wait
            self.last_measure[ts] = ts_wait
        return rewards

    def _waiting_time_reward2(self):
        rewards = {}
        for ts in self.ts_ids:
            ns_wait, ew_wait = self.traffic_signals[ts].get_waiting_time()
            ts_wait = ns_wait + ew_wait
            if ts_wait == 0:
                rewards[ts] = 1.0
            else:
                rewards[ts] = 1.0/ts_wait
        return rewards

    def _sumo_step(self):
        traci.simulationStep()

    def _compute_step_info(self):
        return {
            'step_time': self.sim_step,
            'total_stopped': sum([sum(self.traffic_signals[ts].get_stopped_vehicles_num()) for ts in self.ts_ids]),
            'total_wait_time': sum([self.last_measure[ts] for ts in self.ts_ids])
            #'total_wait_time': sum([sum(self.traffic_signals[ts].get_waiting_time()) for ts in self.ts_ids])
        }

    def close(self):
        traci.close()

    def encode(self, state):
        return self.radix_encode([state[0], self._discretize_elapsed_time(state[1])] + 
                                 [self._discretize_density(d) for d in state[2:]])

    def _discretize_density(self, density):
        if density < 0.1:
            return 0
        elif density < 0.2:
            return 1
        elif density < 0.3:
            return 2
        elif density < 0.4:
            return 3
        elif density < 0.5:
            return 4
        elif density < 0.6:
            return 5
        elif density < 0.7:
            return 6
        elif density < 0.8:
            return 7
        elif density < 0.9:
            return 8
        else:
            return 9

    def _discretize_elapsed_time(self, elapsed):
        elapsed *= self.max_green
        for i in range(self.max_green//self.delta_time):
            if elapsed <= self.delta_time + i*self.delta_time:
                return i
        return self.max_green//self.delta_time -1

    def radix_encode(self, values):
        res = 0
        for i in range(len(self.radix_factors)):
            res = res * self.radix_factors[i] + values[i]
        return int(res)

    def radix_decode(self, value):
        res = [0 for _ in range(len(self.radix_factors))]
        for i in reversed(range(len(self.radix_factors))):
            res[i] = value % self.radix_factors[i]
            value = value // self.radix_factors[i]
        return res



