import sys
import os
import errno
import argparse
from typing import Dict, List, Union
from datetime import datetime
from multiprocessing import Pool
import logging
import numpy as np

from sumo_ql.environment.sumo_environment import SumoEnvironment
from sumo_ql.agent.q_learning import QLAgent, PQLAgent
from sumo_ql.exploration.epsilon_greedy import EpsilonGreedy
from sumo_ql.collector.collector import MainCollector, DefaultCollector

SAVE_OBJ_CHOSEN = False

def run_sim(args: argparse.Namespace, date: datetime = datetime.now(), iteration: int = -1) -> None:
    """Function used to run the simulations, given a set of arguments passed to the script and the iteration (run
    number).

    Args:
        args (argparse.Namespace): namespace containing all the arguments passed to the script
        date (datetime): datetime object that indicates the simulations beggining. Defaults to datetime.now().
        iteration (int, optional): Iteration of simulation run (necessary for log purposes on multiple runs).
        Defaults to -1 (when running only one simulation, then the iteration number is discarded).

    Raises:
        OSError: the function raises an OSError if the log directory can't be created.
        Exception: If any unknown error occurs during the simulation, it raises an exception.
    """
    agents: Dict[str, QLAgent] = dict()
    observations = None
    rewards = None
    env: SumoEnvironment = None
    collect_fit: bool = False
    agent_type = args.agent_type
    opt_travel_time = args.objectives[0] == "TravelTime"

    if args.collect:
        if (collect_fit := args.n_runs == 1):
            print("Making a data fit collect run.")
        else:
            print("Warning: data fit collect only happens in single run simulations.")


    def create_dir(dirname: str) -> None:
        try:
            os.mkdir(f"{dirname}")
        except OSError as error:
            if error.errno != errno.EEXIST:
                print(f"Couldn't create folder {dirname}, error message: {error.strerror}")
                raise OSError(error).with_traceback(error.__traceback__)

    def create_log(dirname: str, date: datetime) -> None:
        """Method that creates a log file that has information of beginning and end of simulations when making multiple
        runs.

        Args:
            dirname (str): directory name where the log will be saved (within the log directory).
            date (datetime): datetime object that is used to know when the multiple runs started.

        Raises:
            OSError: the method raises an OSError if the directory couldn't be created (it doesn't raise the error if
            the directory already exists).
        """
        create_dir("log")
        create_dir(f"log/{dirname}")
        logging.basicConfig(format='%(asctime)s: %(message)s',
                            datefmt='%d-%m-%Y %H:%M:%S',
                            filename=f'log/{dirname}/mult_sims_{date.strftime("%d-%m-%y_%H-%M-%S")}.log',
                            level=logging.INFO)

    def generate_data_collector(cfgfile: str,
                                sim_steps: int,
                                pop_steps: int,
                                comm_succ_rate: float,
                                moving_avg_gap: int,
                                date: datetime,
                                n_runs: int = 1,
                                objectives: List[str] = None) -> MainCollector:
        """Method that generates a data collector based on the information used in the simulation.

        Args:
            cfgfile (str): string with the path to the .sumocfg file that holds network and route information
            sim_steps (int): Time to run the simulation.
            comm_succ_rate (float): The rate (between 0 and 1) in which the communication with the CommDevs succeeds.
            pop_steps (int): Steps to populate the network without using the learning steps.
            moving_avg_gap (int): Step gap to take the travel times moving average measurement.

        Returns:
            DataCollector: class responsible for collecting data from the environment.
        """
        main_simulation_name = str(cfgfile).split('/')[-2]
        additional_folders = list()

        learning_folder = "learning" if pop_steps < sim_steps else "not_learning"
        additional_folders.append(learning_folder)

        if learning_folder == "learning":
            c2i_sr_folder = f"C2I_sr{int(comm_succ_rate * 100)}"
            additional_folders.append(c2i_sr_folder)

        steps_folder = f"steps_{sim_steps // 1000}K"
        additional_folders.append(steps_folder)
        additional_folders.append(f"opt_{'_'.join(objectives)}")

        if n_runs > 1:
            additional_folders.append(f"batch_{date.strftime('%H-%M')}_{n_runs}_runs")
            create_log(main_simulation_name, date)

        return MainCollector(network_name=main_simulation_name,
                             aggregation_interval=moving_avg_gap,
                             additional_folders=additional_folders,
                             param_list=objectives,
                             date=date)

    def create_environment(args: argparse.Namespace) -> SumoEnvironment:
        """Method that creates a SUMO environment given the arguments necessary to it.

        Args:
            args (argparse.Namespace): namespace that contains the arguments passed to the script.

        Returns:
            SumoEnvironment: an environment object used in the learning process.
        """
        data_collector = generate_data_collector(cfgfile=args.cfgfile,
                                                 sim_steps=args.steps,
                                                 pop_steps=args.wait_learn,
                                                 comm_succ_rate=args.comm_succ_rate,
                                                 moving_avg_gap=args.mav,
                                                 date=date,
                                                 n_runs=args.n_runs,
                                                 objectives=args.objectives)

        environment = SumoEnvironment(sumocfg_file=args.cfgfile,
                                      simulation_time=args.steps,
                                      max_vehicles=args.demand,
                                      right_arrival_bonus=args.bonus,
                                      wrong_arrival_penalty=args.penalty,
                                      communication_success_rate=args.comm_succ_rate,
                                      max_comm_dev_queue_size=args.queue_size,
                                      steps_to_populate=args.wait_learn,
                                      use_gui=args.gui,
                                      data_collector=data_collector,
                                      objectives=args.objectives,
                                      fit_data_collect=collect_fit,
                                      min_toll_speed=args.toll_speed,
                                      toll_penalty=args.toll_value)
        return environment

    def run(iteration) -> None:
        """Method that runs a simulation.
        """
        if iteration != -1:
            logging.info("Iteration %s started.", iteration)
        observations = env.reset()
        done = {'__all__': False}

        if agent_type == "PQL":
            network_name = str(args.cfgfile).split('/')[-2]
            chosen_obj_collector = DefaultCollector(1,
                                                    f"results/ChosenObj/{network_name}/{date.strftime('%y_%m_%d')}",
                                                    ["Step"] + args.objectives)

        while not done['__all__']:
            actions = dict()
            for vehicle_id, vehicle in observations.items():
                if vehicle['reinserted'] and vehicle_id not in agents:
                    create_agent(vehicle_id)

            chosen_sum = [0 for obj in range(len(args.objectives))]
            for vehicle_id, vehicle in observations.items():
                if vehicle['ready_to_act'] and vehicle_id in agents:
                    handle_communication(vehicle_id, vehicle['current_state'])
                    current_state = vehicle['current_state']
                    available_actions = vehicle['available_actions']
                    if agent_type == "QL":
                        actions[vehicle_id] = agents[vehicle_id].act(current_state, available_actions)
                    elif agent_type == "PQL":
                        actions[vehicle_id], chosen_obj = agents[vehicle_id].act(current_state, available_actions)
                        if chosen_obj != -1:
                            chosen_sum[chosen_obj] += 1
            if agent_type == "PQL":
                obj_collection_dict = {key: [val] for key, val in zip(env.objectives.objectives_str_list, chosen_sum)}
                obj_collection_dict["Step"] = [env.current_step]
                chosen_obj_collector.append(obj_collection_dict)

            observations, rewards, done, _ = env.step(actions)

            for vehicle_id, reward in rewards.items():
                if vehicle_id in agents:
                    if vehicle_id in done:
                        previous_state = observations[vehicle_id]['previous_state']
                        next_state = observations[vehicle_id]['current_state']
                        handle_learning(vehicle_id, previous_state, next_state, reward)
                    else:
                        previous_state = observations[vehicle_id]['last_link_state']
                        next_state = observations[vehicle_id]['previous_state']
                        handle_learning(vehicle_id, previous_state, next_state, reward)
        env.close()
        if iteration != -1:
            logging.info("Iteration %s finished.", iteration)

    def create_agent(vehicle_id: str) -> None:
        """Method that creates a learning agent and puts it in the agents dictionary.

        Args:
            vehicle_id (str): vehicle id to identify the agent.
        """
        if agent_type == "QL":
            agents[vehicle_id] = QLAgent(action_space=env.action_space,
                                         exploration_strategy=EpsilonGreedy(initial_epsilon=0.05, min_epsilon=0.05))
        elif agent_type == "PQL":
            agents[vehicle_id] = PQLAgent(action_space=env.action_space,
                                         exploration_strategy=EpsilonGreedy(initial_epsilon=0.05, min_epsilon=0.05))
        else:
            raise RuntimeError(f"Agent {agent_type} not recognized. Agents should be QL or PQL.")

    def handle_learning(vehicle_id: str, origin_node: str, destination_node: str, reward: np.array) -> None:
        """Method that takes care of the learning process for the agent given.

        Args:
            vehicle_id (str): ID of the vehicle to process learning.
            origin_node (str): origin node the agent took the action.
            destination_node (str): destination node the action leaded to.
            reward (int): reward received from the action taken.

        Raises:
            Exception: it raises an Exception if anything goes wrong.
        """
        try:
            action = env.get_action(origin_node, destination_node)
            if agent_type == "QL":
                obj = 0 if opt_travel_time else 1
                agents[vehicle_id].learn(action, origin_node, destination_node, reward[obj])
            elif agent_type == "PQL":
                agents[vehicle_id].learn(action, origin_node, destination_node, reward)

        except Exception as exception:
            print(f"{vehicle_id = }")
            print(f"{observations = }")
            print(f"{rewards = }")
            raise Exception(exception).with_traceback(exception.__traceback__)

    def handle_communication(vehicle_id: str, state: str) -> None:
        """Method that retrieves CommDevs information if the C2I communication succeeds to update the agent's knowledge
        about the network.

        Args:
            vehicle_id (str): ID of the vehicle that will communicate with the CommDev.
            state (str): the state the CommDev is present.
        """
        comm_dev = env.get_comm_dev(state)
        if comm_dev.communication_success:
            if agent_type == "QL":
                expected_rewards = comm_dev.get_outgoing_links_expected_rewards()
                for link, expected_reward in expected_rewards.items():
                    origin = env.get_link_origin(link)
                    destination = env.get_link_destination(link)
                    handle_learning(vehicle_id, origin, destination, expected_reward)
            else:
                print("Warning: communication not available for non QL agents.")

    # Run the simulation
    env = create_environment(args)
    run(iteration)

def parse_args() -> Union[argparse.Namespace, argparse.ArgumentParser]:
    """Method that implements the argument parser for the script.

    Returns:
        Union[argparse.Namespace, argparse.ArgumentParser]: union between parsed arguments and argument parser
    """
    parser = argparse.ArgumentParser(prog='Script to run SUMO environment with multiagent Q-Learning algorithm')

    parser.add_argument("-c", "--cfg-file", action="store", dest="cfgfile",
                       help="define the config SUMO file (mandatory)")
    parser.add_argument("-d", "--demand", action="store", type=int, dest="demand",
                       default=750, help="desired network demand (default = 750)")
    parser.add_argument("-s", "--steps", action="store", type=int, default=60000,
                       help="number of max steps (default = 60000)", dest="steps")
    parser.add_argument("-w", "--wait-learning", action="store", type=int, default=3000, dest="wait_learn",
                       help="Time steps before agents start the learning (default = 3000)")
    parser.add_argument("-g", "--gui", action="store_true", dest="gui", default=False,
                       help="uses SUMO GUI instead of CLI")
    parser.add_argument("-m", "--mav", action="store", type=int, dest="mav", default=100,
                       help="Moving gap size (default = 100 steps)")
    parser.add_argument("-r", "--success-rate", action="store", type=float, dest="comm_succ_rate", default=0.0,
                       help="Communication success rate (default = 0.0)")
    parser.add_argument("-q", "--queue-size", action="store", type=int, dest="queue_size", default=30,
                       help="CommDev queue size (default = 30)")
    parser.add_argument("-b", "--bonus", action="store", type=int, dest="bonus", default=1000,
                       help="Bonus agents receive by finishing their trip at the right destination (default = 1000)")
    parser.add_argument("-p", "--penalty", action="store", type=int, dest="penalty", default=1000,
                       help="Penalty agents receive by finishing their trip at the wrong destination (default = 1000)")
    parser.add_argument("-n", "--number-of-runs", action="store", type=int, dest="n_runs", default=1,
                       help="Number of multiple simulation runs (default = 1)")
    parser.add_argument("--parallel", action="store_true", dest="parallel", default=False,
                       help="Set the script to run simulations in parallel using number of available CPU")
    parser.add_argument("--objectives", action="store", nargs="+", dest="objectives", default=["TravelTime"],
                       help="List with objective params to use separated by a single space (default = [TravelTime])")
    parser.add_argument("--collect", action="store_true", dest="collect", default=False,
                       help="Set the run to collect info about the reward values to use as normalizer latter.")
    parser.add_argument("-a", "--agent-type", action="store", dest="agent_type", default="QL",
                        help="Set the agent type to use in simulation. (Must be QL or PQL. Default = QL)")
    parser.add_argument("-t", "--toll-speed", action="store", dest="toll_speed", default=-1, type=float,
                        help="Set the min speed in link to impose a toll for emission. (default = -1, toll not used)")
    parser.add_argument("-v", "--toll-value", action="store", dest="toll_value", default=-1, type=float,
                        help="Set the toll value to be added as penalty to emission. (default = -1, toll not used)")

    return parser.parse_args(), parser

def main():
    """Main script funcion that starts the running process.
    """
    options, parser = parse_args()
    if not options.cfgfile:
        print('Wrong usage of script!')
        print()
        parser.print_help()
        sys.exit()

    if options.n_runs > 1:
        curr_date = datetime.now()
        if options.parallel:
            sys.setrecursionlimit(3000)
            with Pool(processes=os.cpu_count()) as pool:
                _ = [pool.apply_async(run_sim, args=(options, curr_date, it)) for it in range(options.n_runs)]
                pool.close()
                pool.join()
        else:
            for i in range(options.n_runs):
                run_sim(options, curr_date, i)
    else:
        run_sim(options)


if __name__ == '__main__':
    main()
