import argparse
import os
import sys
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare the environment variable 'SUMO_HOME'")
import pandas as pd
import ray
from ray.rllib.agents.a3c.a3c import A3CAgent
from ray.rllib.agents.a3c.a3c_tf_policy_graph import A3CPolicyGraph
from ray.tune.registry import register_env
from gym import spaces
import numpy as np
from environment.env import SumoEnvironment
import traci


if __name__ == '__main__':
    ray.init()

    register_env("2way-single-intersection", lambda _: SumoEnvironment(net_file='nets/2way-single-intersection/single-intersection.net.xml',
                                                    route_file='nets/2way-single-intersection/single-intersection.rou.xml',
                                                    use_gui=True,
                                                    num_seconds=100000,
                                                    time_to_load_vehicles=120,
                                                    max_depart_delay=0,
                                                    phases=[
                                                        traci.trafficlight.Phase(32000, 32000, 32000, "GGrrrrGGrrrr"),  
                                                        traci.trafficlight.Phase(2000, 2000, 2000, "yyrrrryyrrrr"),
                                                        traci.trafficlight.Phase(32000, 32000, 32000, "rrGrrrrrGrrr"),   
                                                        traci.trafficlight.Phase(2000, 2000, 2000, "rryrrrrryrrr"),
                                                        traci.trafficlight.Phase(32000, 32000, 32000, "rrrGGrrrrGGr"),   
                                                        traci.trafficlight.Phase(2000, 2000, 2000, "rrryyrrrryyr"),
                                                        traci.trafficlight.Phase(32000, 32000, 32000, "rrrrrGrrrrrG"), 
                                                        traci.trafficlight.Phase(2000, 2000, 2000, "rrrrryrrrrry")
                                                        ]))

    trainer = A3CAgent(env="2way-single-intersection", config={
        "multiagent": {
            "policy_graphs": {
                '0': (A3CPolicyGraph, spaces.Box(low=np.zeros(13), high=np.ones(13)), spaces.Discrete(4), {})
            },
            "policy_mapping_fn": lambda id: '0'  # Traffic lights are always controlled by this policy
        },
        "lr": 0.0001,
    })
    while True:
        print(trainer.train())  # distributed training step