import time
from typing import List, Tuple, Type, Union, Callable, Optional, Dict, Any

import gym
from gym import spaces
import torch as th
import torch.nn.functional as F
import copy
from scipy.special import softmax
# Check if tensorboard is available for pytorch
# TODO: finish tensorboard integration
# try:
#     from torch.utils.tensorboard import SummaryWriter
# except ImportError:
#     SummaryWriter = None
import numpy as np

from torch.utils.tensorboard import SummaryWriter
from gym_idsgame.agents.training_agents.openai_baselines.common.base_class import BaseRLModel
from stable_baselines3.common.type_aliases import GymEnv, MaybeCallback
from stable_baselines3.common.buffers import RolloutBuffer
from gym_idsgame.agents.training_agents.openai_baselines.common.utils import get_schedule_fn

from gym_idsgame.agents.training_agents.openai_baselines.common.vec_env.base_vec_env import VecEnv
from gym_idsgame.agents.training_agents.openai_baselines.common.callbacks import BaseCallback
from gym_idsgame.agents.training_agents.openai_baselines.common.ppo.ppo_policies import PPOPolicy
from gym_idsgame.agents.training_agents.policy_gradient.pg_agent_config import PolicyGradientAgentConfig
from gym_idsgame.agents.training_agents.openai_baselines.common.common_policies import (BasePolicy, register_policy, MlpExtractor,
                                                                                     create_sde_features_extractor, NatureCNN,
                                                                                     BaseFeaturesExtractor, FlattenExtractor)
from gym_idsgame.agents.bot_agents.defend_minimal_value_bot_agent import DefendMinimalValueBotAgent
from gym_idsgame.agents.bot_agents.attack_maximal_value_bot_agent import AttackMaximalValueBotAgent
from gym_idsgame.agents.bot_agents.random_defense_bot_agent import RandomDefenseBotAgent
from gym_idsgame.agents.bot_agents.random_attack_bot_agent import RandomAttackBotAgent

class PPO(BaseRLModel):
    """
    Proximal Policy Optimization algorithm (PPO) (clip version)

    Paper: https://arxiv.org/abs/1707.06347
    Code: This implementation borrows code from OpenAI Spinning Up (https://github.com/openai/spinningup/)
    https://github.com/ikostrikov/pytorch-a2c-ppo-acktr-gail and
    and Stable Baselines (PPO2 from https://github.com/hill-a/stable-baselines)

    Introduction to PPO: https://spinningup.openai.com/en/latest/algorithms/ppo.html

    :param policy: (PPOPolicy or str) The policy model to use (MlpPolicy, CnnPolicy, ...)
    :param env: (Gym environment or str) The environment to learn from (if registered in Gym, can be str)
    :param learning_rate: (float or callable) The learning rate, it can be a function
        of the current progress (from 1 to 0)
    :param n_steps: (int) The number of steps to run for each environment per update
        (i.e. batch size is n_steps * n_env where n_env is number of environment copies running in parallel)
    :param batch_size: (int) Minibatch size
    :param n_epochs: (int) Number of epoch when optimizing the surrogate loss
    :param gamma: (float) Discount factor
    :param gae_lambda: (float) Factor for trade-off of bias vs variance for Generalized Advantage Estimator
    :param clip_range: (float or callable) Clipping parameter, it can be a function of the current progress
        (from 1 to 0).
    :param clip_range_vf: (float or callable) Clipping parameter for the value function,
        it can be a function of the current progress (from 1 to 0).
        This is a parameter specific to the OpenAI implementation. If None is passed (default),
        no clipping will be done on the value function.
        IMPORTANT: this clipping depends on the reward scaling.
    :param ent_coef: (float) Entropy coefficient for the loss calculation
    :param vf_coef: (float) Value function coefficient for the loss calculation
    :param max_grad_norm: (float) The maximum value for the gradient clipping
    :param use_sde: (bool) Whether to use generalized State Dependent Exploration (gSDE)
        instead of action noise exploration (default: False)
    :param sde_sample_freq: (int) Sample a new noise matrix every n steps when using gSDE
        Default: -1 (only sample at the beginning of the rollout)
    :param target_kl: (float) Limit the KL divergence between updates,
        because the clipping is not enough to prevent large update
        see issue #213 (cf https://github.com/hill-a/stable-baselines/issues/213)
        By default, there is no limit on the kl div.
    :param tensorboard_log: (str) the log location for tensorboard (if None, no logging)
    :param create_eval_env: (bool) Whether to create a second environment that will be
        used for evaluating the agent periodically. (Only available when passing string for the environment)
    :param policy_kwargs: (dict) additional arguments to be passed to the policy on creation
    :param verbose: (int) the verbosity level: 0 no output, 1 info, 2 debug
    :param seed: (int) Seed for the pseudo random generators
    :param device: (str or th.device) Device (cpu, cuda, ...) on which the code should be run.
        Setting it to auto, the code will be run on the GPU if possible.
    :param _init_setup_model: (bool) Whether or not to build the network at the creation of the instance
    """

    def __init__(self, policy: Union[str, Type[PPOPolicy]],
                 env: Union[GymEnv, str],
                 learning_rate: Union[float, Callable] = 3e-4,
                 n_steps: int = 2048,
                 batch_size: Optional[int] = 64,
                 n_epochs: int = 10,
                 gamma: float = 0.99,
                 gae_lambda: float = 0.95,
                 clip_range: float = 0.2,
                 clip_range_vf: Optional[float] = None,
                 ent_coef: float = 0.0,
                 vf_coef: float = 0.5,
                 max_grad_norm: float = 0.5,
                 use_sde: bool = False,
                 sde_sample_freq: int = -1,
                 target_kl: Optional[float] = None,
                 tensorboard_log: Optional[str] = None,
                 create_eval_env: bool = False,
                 policy_kwargs: Optional[Dict[str, Any]] = None,
                 verbose: int = 0,
                 seed: Optional[int] = None,
                 device: Union[th.device, str] = 'auto',
                 _init_setup_model: bool = True,
                 pg_agent_config : PolicyGradientAgentConfig = None):
        super(PPO, self).__init__(policy, env, PPOPolicy, learning_rate, policy_kwargs=policy_kwargs,
                                  verbose=verbose, device=device, use_sde=use_sde, sde_sample_freq=sde_sample_freq,
                                  create_eval_env=create_eval_env, support_multi_env=True, seed=seed,
                                  pg_agent_config=pg_agent_config)

        self.batch_size = batch_size
        self.n_epochs = n_epochs
        self.n_steps = n_steps
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_range = clip_range
        self.clip_range_vf = clip_range_vf
        self.ent_coef = ent_coef
        self.vf_coef = vf_coef
        self.max_grad_norm = max_grad_norm
        self.attacker_rollout_buffer = None
        self.target_kl = target_kl
        self.tensorboard_log = tensorboard_log
        self.tb_writer = None
        self.pg_agent_config = pg_agent_config
        self.iteration = 0
        self.train_attacker = True
        self.train_defender = True
        if self.pg_agent_config.opponent_pool and self.pg_agent_config.opponent_pool_config is not None:
            self.attacker_pool = []
            self.defender_pool = []
            self.train_attacker = True
            self.train_defender = False
            if self.pg_agent_config.baselines_in_pool:
                if self.pg_agent_config.opponent_pool_config.quality_scores:
                    self.defender_pool.append([DefendMinimalValueBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config), self.pg_agent_config.opponent_pool_config.initial_quality])
                    self.defender_pool.append(
                        [RandomDefenseBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config), self.pg_agent_config.opponent_pool_config.initial_quality])
                    self.attacker_pool.append(
                        [AttackMaximalValueBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config, self.env.envs[0].idsgame_env),
                         self.pg_agent_config.opponent_pool_config.initial_quality])
                    self.attacker_pool.append(
                        [RandomAttackBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config,
                                                    self.env.envs[0].idsgame_env),
                         self.pg_agent_config.opponent_pool_config.initial_quality])
                else:
                    self.defender_pool.append(DefendMinimalValueBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config))
                    self.defender_pool.append([RandomDefenseBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config)])
                    self.attacker_pool.append(RandomAttackBotAgent(self.env.envs[0].idsgame_env.idsgame_config.game_config,
                                                    self.env.envs[0].idsgame_env))
                #self.attacker_pool.append()
        try:
            self.tensorboard_writer = SummaryWriter(self.pg_agent_config.tensorboard_dir)
            self.tensorboard_writer.add_hparams(self.pg_agent_config.hparams_dict(), {})
        except:
            pass

        if _init_setup_model:
            self._setup_model()

    def _setup_model(self) -> None:
        self._setup_lr_schedule()
        self.set_random_seed(self.seed)
        self.attacker_rollout_buffer = RolloutBuffer(self.n_steps, self.attacker_observation_space,
                                                     self.attacker_action_space, self.device,
                                                     gamma=self.gamma, gae_lambda=self.gae_lambda,
                                                     n_envs=self.n_envs)
        self.defender_rollout_buffer = RolloutBuffer(self.n_steps, self.defender_observation_space,
                                                     self.defender_action_space, self.device,
                                                     gamma=self.gamma, gae_lambda=self.gae_lambda,
                                                     n_envs=self.n_envs)
        if not self.pg_agent_config.cnn_feature_extractor:
            feature_extractor_class = FlattenExtractor
        else:
            feature_extractor_class = NatureCNN
        self.attacker_policy = PPOPolicy(self.attacker_observation_space, self.attacker_action_space,
                                         self.lr_schedule_a, use_sde=self.use_sde, device=self.device,
                                         pg_agent_config=self.pg_agent_config,
                                         features_extractor_class=feature_extractor_class,
                                         **self.policy_kwargs)
        self.attacker_policy = self.attacker_policy.to(self.device)

        self.defender_policy = PPOPolicy(self.defender_observation_space, self.defender_action_space,
                                         self.lr_schedule_d, use_sde=self.use_sde, device=self.device,
                                         pg_agent_config=self.pg_agent_config,
                                         features_extractor_class=feature_extractor_class,
                                         **self.policy_kwargs)
        self.defender_policy = self.defender_policy.to(self.device)

        self.clip_range = get_schedule_fn(self.clip_range)
        if self.clip_range_vf is not None:
            if isinstance(self.clip_range_vf, (float, int)):
                assert self.clip_range_vf > 0, ('`clip_range_vf` must be positive, '
                                                'pass `None` to deactivate vf clipping')

            self.clip_range_vf = get_schedule_fn(self.clip_range_vf)

        if self.pg_agent_config.opponent_pool and self.pg_agent_config.opponent_pool_config is not None:
            self.add_model_to_pool(attacker=True)
            self.add_model_to_pool(attacker=False)

            self.defender_opponent_idx = self.sample_opponent(attacker=False)
            if self.pg_agent_config.opponent_pool_config.quality_scores:
                self.defender_opponent = self.defender_pool[self.defender_opponent_idx][0]
            else:
                self.defender_opponent = self.defender_pool[self.defender_opponent_idx]

            self.attacker_opponent_idx = self.sample_opponent(attacker=True)
            if self.pg_agent_config.opponent_pool_config.quality_scores:
                self.attacker_opponent = self.attacker_pool[self.attacker_opponent_idx][0]
            else:
                self.attacker_opponent = self.attacker_pool[self.attacker_opponent_idx]

    def predict(self, observation: np.ndarray,
                state: Optional[np.ndarray] = None,
                mask: Optional[np.ndarray] = None,
                deterministic: bool = False, attacker = True) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """
        Get the model's action(s) from an observation

        :param observation: (np.ndarray) the input observation
        :param state: (Optional[np.ndarray]) The last states (can be None, used in recurrent policies)
        :param mask: (Optional[np.ndarray]) The last masks (can be None, used in recurrent policies)
        :param deterministic: (bool) Whether or not to return deterministic actions.
        :return: (Tuple[np.ndarray, Optional[np.ndarray]]) the model's action and the next state
            (used in recurrent policies)
        """
        if attacker:
            return self.attacker_policy._predict(observation, self.env.envs[0], deterministic, device=self.device,
                                                 attacker=True)
        else:
            return self.defender_policy._predict(observation, self.env.envs[0], deterministic, device=self.device,
                                                 attacker=False)

    def collect_rollouts(self,
                         env: VecEnv,
                         callback: BaseCallback,
                         attacker_rollout_buffer: RolloutBuffer,
                         defender_rollout_buffer: RolloutBuffer,
                         n_rollout_steps: int = 256) -> Union[bool, List, List, List]:

        assert self._last_obs_a is not None, "No previous attacker observation was provided"
        assert self._last_obs_d is not None, "No previous defender observation was provided"
        n_steps = 0
        if self.pg_agent_config.attacker:
            attacker_rollout_buffer.reset()
        if self.pg_agent_config.defender:
            defender_rollout_buffer.reset()

        # Sample new weights for the state dependent exploration
        if self.use_sde:
            if self.pg_agent_config.attacker:
                self.attacker_policy.reset_noise(env.num_envs)
            if self.pg_agent_config.defender:
                self.defender_policy.reset_noise(env.num_envs)

        # Avg metrics
        episode_attacker_rewards = []
        episode_defender_rewards = []
        episode_steps = []

        # Per episode metrics
        episode_attacker_reward = 0
        episode_defender_reward = 0
        episode_step = 0

        callback.on_rollout_start()
        while n_steps < n_rollout_steps:
            if self.use_sde and self.sde_sample_freq > 0 and n_steps % self.sde_sample_freq == 0:
                # Sample a new noise matrix
                if self.pg_agent_config.attacker:
                    self.attacker_policy.reset_noise(env.num_envs)
                if self.pg_agent_config.defender:
                    self.defender_policy.reset_noise(env.num_envs)

            with th.no_grad():

                # Default actions
                attacker_actions = [0]
                defender_actions = [0]

                # Convert to pytorch tensor
                obs_tensor_a = th.as_tensor(self._last_obs_a).to(self.device)
                obs_tensor_d = th.as_tensor(self._last_obs_d).to(self.device)
                if self.pg_agent_config.attacker and self.train_attacker:
                    attacker_actions, attacker_values, attacker_log_probs = self.attacker_policy.forward(
                        obs_tensor_a, self.env.envs[0], device=self.device, attacker=True)
                    attacker_actions = attacker_actions.cpu().numpy()

                    if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                        if isinstance(self.defender_opponent, PPOPolicy):
                            defender_actions, defender_values, defender_log_probs = self.defender_opponent.forward(
                                obs_tensor_d, self.env.envs[0], device=self.device, attacker=False)
                            defender_actions = defender_actions.cpu().numpy()
                        else:
                            action = self.defender_opponent.action(self.env.envs[0].idsgame_env.state)
                            defender_actions = np.array([action])

                if self.pg_agent_config.defender and self.train_defender:
                    defender_actions, defender_values, defender_log_probs = self.defender_policy.forward(
                        obs_tensor_d,  self.env.envs[0], device=self.device, attacker=False)
                    defender_actions = defender_actions.cpu().numpy()

                    if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                        if isinstance(self.defender_opponent, PPOPolicy):
                            attacker_actions, attacker_values, attacker_log_probs = self.attacker_opponent.forward(
                                obs_tensor_a, self.env.envs[0], device=self.device, attacker=True)
                            attacker_actions = attacker_actions.cpu().numpy()
                        else:
                            action = self.attacker_opponent.action(self.env.envs[0].idsgame_env.state)
                            attacker_actions = np.array([action])

            # Rescale and perform action
            clipped_attacker_actions = attacker_actions
            clipped_defender_actions = defender_actions
            # Clip the attacker_actions to avoid out of bound error
            if isinstance(self.attacker_action_space, gym.spaces.Box):
                clipped_attacker_actions = np.clip(attacker_actions, self.attacker_action_space.low, self.attacker_action_space.high)
                clipped_defender_actions = np.clip(defender_actions, self.attacker_action_space.low, self.attacker_action_space.high)

            joint_actions = np.array([[clipped_attacker_actions, clipped_defender_actions]])
            new_a_obs, new_d_obs, a_rewards, d_rewards, dones, infos = env.step(joint_actions, update_stats=True)

            if callback.on_step() is False:
                return False


            # Record step metrics
            self._update_info_buffer(infos)
            n_steps += 1
            self.num_timesteps += env.num_envs
            episode_attacker_reward += a_rewards
            episode_defender_reward += d_rewards
            episode_step +=1

            if isinstance(self.attacker_action_space, gym.spaces.Discrete):
                # Reshape in case of discrete action
                if self.pg_agent_config.attacker:
                    attacker_actions = attacker_actions.reshape(-1, 1)
                if self.pg_agent_config.defender:
                    defender_actions = defender_actions.reshape(-1, 1)

            if self.pg_agent_config.attacker:
                if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                    if self.train_attacker:
                        attacker_rollout_buffer.add(self._last_obs_a, attacker_actions, a_rewards, dones, attacker_values, attacker_log_probs)
                else:
                    attacker_rollout_buffer.add(self._last_obs_a, attacker_actions, a_rewards, dones, attacker_values,
                                                attacker_log_probs)
            if self.pg_agent_config.defender:
                if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                    if self.train_defender:
                        defender_rollout_buffer.add(self._last_obs_d, defender_actions, d_rewards, dones, defender_values,
                                                    defender_log_probs)
                else:
                    defender_rollout_buffer.add(self._last_obs_d, defender_actions, d_rewards, dones, defender_values,
                                                defender_log_probs)
            self._last_obs_a = new_a_obs
            self._last_obs_d = new_d_obs

            if dones:
                # Record episode metrics
                self.num_train_games += 1
                self.num_train_games_total += 1
                if env.envs[0].prev_episode_hacked:
                    self.num_train_hacks += 1
                    self.num_train_hacks_total += 1
                episode_attacker_rewards.append(episode_attacker_reward)
                episode_defender_rewards.append(episode_defender_reward)
                episode_steps.append(episode_step)
                episode_attacker_reward = 0
                episode_defender_reward = 0
                episode_step = 0

                # Update opponent pool qualities
                if self.pg_agent_config.opponent_pool and self.pg_agent_config.opponent_pool_config is not None \
                        and self.pg_agent_config.opponent_pool_config.quality_scores:
                    if self.train_attacker and self.defender_opponent_idx is not None and env.envs[0].prev_episode_hacked:
                        self.update_quality_score(self.defender_opponent_idx, attacker=False)
                    if self.train_defender and self.attacker_opponent_idx is not None and not env.envs[0].prev_episode_hacked:
                        self.update_quality_score(self.attacker_opponent_idx, attacker=True)

                # Sample new opponents
                if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                    if self.pg_agent_config.attacker and self.train_attacker:
                        if np.random.rand() < self.pg_agent_config.opponent_pool_config.pool_prob:
                            self.defender_opponent_idx = self.sample_opponent(attacker=False)
                            if self.pg_agent_config.opponent_pool_config.quality_scores:
                                self.defender_opponent = self.defender_pool[self.defender_opponent_idx][0]
                            else:
                                self.defender_opponent = self.defender_pool[self.defender_opponent_idx]

                    if self.pg_agent_config.defender and self.train_defender:
                        if np.random.rand() < self.pg_agent_config.opponent_pool_config.pool_prob:
                            self.attacker_opponent_idx = self.sample_opponent(attacker=True)
                            if self.pg_agent_config.opponent_pool_config.quality_scores:
                                self.attacker_opponent = self.attacker_pool[self.attacker_opponent_idx][0]
                            else:
                                self.attacker_opponent = self.attacker_pool[self.attacker_opponent_idx]


        if self.pg_agent_config.attacker:
            attacker_rollout_buffer.compute_returns_and_advantage(attacker_values, dones=dones)
        if self.pg_agent_config.defender:
            defender_rollout_buffer.compute_returns_and_advantage(defender_values, dones=dones)

        callback.on_rollout_end()

        return True, episode_attacker_rewards, episode_defender_rewards, episode_steps

    def train(self, n_epochs: int, batch_size: int = 64, attacker=True) -> None:
        # Update optimizer learning rate
        if attacker:
            self._update_learning_rate(self.attacker_policy.optimizer, attacker=True)
            lr = self.attacker_policy.optimizer.param_groups[0]["lr"]
        else:
            self._update_learning_rate(self.defender_policy.optimizer, attacker=False)
            lr = self.defender_policy.optimizer.param_groups[0]["lr"]
        # Compute current clip range
        clip_range = self.clip_range(self._current_progress)
        # Optional: clip range for the value function
        if self.clip_range_vf is not None:
            clip_range_vf = self.clip_range_vf(self._current_progress)

        entropy_losses, all_kl_divs = [], []
        pg_losses, value_losses = [], []
        clip_fractions = []

        # train for gradient_steps epochs
        for epoch in range(n_epochs):
            approx_kl_divs = []
            # Do a complete pass on the rollout buffer
            if attacker:
                rollout_buffer = self.attacker_rollout_buffer
            else:
                rollout_buffer = self.defender_rollout_buffer
            for rollout_data in rollout_buffer.get(batch_size):
                actions = rollout_data.actions
                if isinstance(self.attacker_action_space, spaces.Discrete):
                    # Convert discrete action from float to long
                    actions = rollout_data.actions.long().flatten()

                # Re-sample the noise matrix because the log_std has changed
                # TODO: investigate why there is no issue with the gradient
                # if that line is commented (as in SAC)
                if self.use_sde:
                    if attacker and self.train_attacker:
                        self.attacker_policy.reset_noise(batch_size)
                    else:
                        self.defender_policy.reset_noise(batch_size)

                if attacker and self.train_attacker:
                    values, log_prob, entropy = self.attacker_policy.evaluate_actions(
                        rollout_data.observations, actions, self.env.envs[0], attacker=True)
                else:
                    values, log_prob, entropy = self.defender_policy.evaluate_actions(
                        rollout_data.observations, actions, self.env.envs[0], attacker=False)
                values = values.flatten()
                # Normalize advantage
                advantages = rollout_data.advantages
                advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

                # ratio between old and new policy, should be one at the first iteration
                ratio = th.exp(log_prob - rollout_data.old_log_prob)
                # clipped surrogate loss
                policy_loss_1 = advantages * ratio
                policy_loss_2 = advantages * th.clamp(ratio, 1 - clip_range, 1 + clip_range)
                policy_loss = -th.min(policy_loss_1, policy_loss_2).mean()

                # Logging
                pg_losses.append(policy_loss.item())
                clip_fraction = th.mean((th.abs(ratio - 1) > clip_range).float()).item()
                clip_fractions.append(clip_fraction)

                if self.clip_range_vf is None:
                    # No clipping
                    values_pred = values
                else:
                    # Clip the different between old and new value
                    # NOTE: this depends on the reward scaling
                    values_pred = rollout_data.old_values + th.clamp(values - rollout_data.old_values, -clip_range_vf,
                                                                     clip_range_vf)
                # Value loss using the TD(gae_lambda) target
                value_loss = F.mse_loss(rollout_data.returns, values_pred)
                value_losses.append(value_loss.item())

                # Entropy loss favor exploration
                if entropy is None:
                    # Approximate entropy when no analytical form
                    entropy_loss = -log_prob.mean()
                else:
                    entropy_loss = -th.mean(entropy)

                entropy_losses.append(entropy_loss.item())
                loss = policy_loss + self.ent_coef * entropy_loss + self.vf_coef * value_loss

                # Optimization step
                if attacker:
                    self.attacker_policy.optimizer.zero_grad()
                else:
                    self.defender_policy.optimizer.zero_grad()
                loss.backward()
                # Clip grad norm
                if attacker:
                    th.nn.utils.clip_grad_norm_(self.attacker_policy.parameters(), self.max_grad_norm)
                    self.attacker_policy.optimizer.step()
                else:
                    th.nn.utils.clip_grad_norm_(self.defender_policy.parameters(), self.max_grad_norm)
                    self.defender_policy.optimizer.step()
                approx_kl_divs.append(th.mean(rollout_data.old_log_prob - log_prob).detach().cpu().numpy())

            all_kl_divs.append(np.mean(approx_kl_divs))

            if self.target_kl is not None and np.mean(approx_kl_divs) > 1.5 * self.target_kl:
                print(f"Early stopping at step {epoch} due to reaching max kl: {np.mean(approx_kl_divs):.2f}")
                break

        self._n_updates += n_epochs

        # explained_var = explained_variance(self.attacker_rollout_buffer.returns.flatten(),
        #                                    self.attacker_rollout_buffer.values.flatten())

        # logger.logkv("n_updates", self._n_updates)
        # logger.logkv("clip_fraction", np.mean(clip_fraction))
        # logger.logkv("clip_range", clip_range)
        # if self.clip_range_vf is not None:
        #     logger.logkv("clip_range_vf", clip_range_vf)
        #
        # logger.logkv("approx_kl", np.mean(approx_kl_divs))
        # logger.logkv("explained_variance", explained_var)
        # logger.logkv("entropy_loss", np.mean(entropy_losses))
        # logger.logkv("policy_gradient_loss", np.mean(pg_losses))
        # logger.logkv("value_loss", np.mean(value_losses))
        # if hasattr(self.policy, 'log_std'):
        #     logger.logkv("std", th.exp(self.policy.log_std).mean().item())
        return np.mean(entropy_losses), np.mean(pg_losses), np.mean(value_losses), lr

    def learn(self,
              total_timesteps: int,
              callback: MaybeCallback = None,
              log_interval: int = 1,
              eval_env: Optional[GymEnv] = None,
              eval_freq: int = -1,
              n_eval_episodes: int = 5,
              tb_log_name: str = "PPO",
              eval_log_path: Optional[str] = None,
              reset_num_timesteps: bool = True) -> 'PPO':

        self.pg_agent_config.logger.info("Setting up Training Configuration")
        print("Setting up Training Configuration")
        self.iteration = 0
        callback = self._setup_learn(eval_env, callback, eval_freq,
                                     n_eval_episodes, eval_log_path, reset_num_timesteps)

        # if self.tensorboard_log is not None and SummaryWriter is not None:
        #     self.tb_writer = SummaryWriter(log_dir=os.path.join(self.tensorboard_log, tb_log_name))

        callback.on_training_start(locals(), globals())
        self.pg_agent_config.logger.info("Starting training, max time steps:{}".format(total_timesteps))
        print("Starting training, max time steps:{}".format(total_timesteps))
        self.pg_agent_config.logger.info(self.pg_agent_config.to_str())

        # Tracking metrics

        # Avg metrics
        episode_attacker_rewards = []
        episode_defender_rewards = []
        episode_steps = []
        episode_avg_attacker_loss = []
        episode_avg_defender_loss = []
        attacker_lr = 0.0
        defender_lr = 0.0

        if self.pg_agent_config.opponent_pool and self.pg_agent_config.opponent_pool_config is not None:
            attacker_pool_iteration = 0
            defender_pool_iteration = 0

        if self.pg_agent_config.alternating_optimization:
            optimization_iteration = 0

        while self.num_timesteps < total_timesteps:
            continue_training, rollouts_attacker_rewards, rollouts_defender_rewards, rollouts_steps = \
                self.collect_rollouts(self.env, callback, self.attacker_rollout_buffer, self.defender_rollout_buffer,
                                      n_rollout_steps=self.n_steps)
            episode_attacker_rewards.extend(rollouts_attacker_rewards)
            episode_defender_rewards.extend(rollouts_defender_rewards)
            episode_steps.extend(rollouts_defender_rewards)

            if continue_training is False:
                break

            self.iteration += 1
            self._update_current_progress(self.num_timesteps, total_timesteps)

            # Display training infos
            if self.iteration % self.pg_agent_config.train_log_frequency == 0:
                if self.num_train_games > 0 and self.num_train_games_total > 0:
                    self.train_hack_probability = self.num_train_hacks / self.num_train_games
                    self.train_cumulative_hack_probability = self.num_train_hacks_total / self.num_train_games_total
                else:
                    self.train_hack_probability = 0.0
                    self.train_cumulative_hack_probability = 0.0
                a_pool = 0
                d_pool = 0
                if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                    a_pool = len(self.attacker_pool)
                    d_pool = len(self.defender_pool)
                self.log_metrics(iteration=self.iteration, result=self.train_result,
                                 attacker_episode_rewards=episode_attacker_rewards,
                                 defender_episode_rewards=episode_defender_rewards, episode_steps=episode_steps,
                                 episode_avg_attacker_loss=episode_avg_attacker_loss,
                                 episode_avg_defender_loss=episode_avg_defender_loss,
                                 eval=False, update_stats=True, lr_attacker=self.lr_schedule_a(self._current_progress),
                                 lr_defender=self.lr_schedule_d(self._current_progress),
                                 total_num_episodes=self.num_train_games_total,
                                 train_attacker=(self.pg_agent_config.attacker and self.train_attacker),
                                 train_defender=(self.pg_agent_config.defender and self.train_defender),
                                 a_pool=a_pool, d_pool=d_pool
                                 )
                episode_attacker_rewards = []
                episode_defender_rewards = []
                episode_avg_attacker_loss = []
                episode_avg_defender_loss = []
                episode_steps = []
                self.num_train_games = 0
                self.num_train_hacks = 0

                if self.pg_agent_config.alternating_optimization and self.pg_agent_config.opponent_pool:
                    if self.train_attacker:
                        attacker_pool_iteration += 1
                    if self.train_defender:
                        defender_pool_iteration += 1

                if self.pg_agent_config.alternating_optimization:
                    optimization_iteration += 1

                # If using opponent pool, update the pool
                if self.pg_agent_config.opponent_pool and self.pg_agent_config.opponent_pool_config is not None:
                    if self.train_defender:
                        if defender_pool_iteration > self.pg_agent_config.opponent_pool_config.pool_increment_period:
                            self.add_model_to_pool(attacker=False)
                            defender_pool_iteration = 0

                    if self.train_attacker:
                        if attacker_pool_iteration > self.pg_agent_config.opponent_pool_config.pool_increment_period:
                            self.add_model_to_pool(attacker=True)
                            attacker_pool_iteration = 0


            # Save models every <self.config.checkpoint_frequency> iterations
            if self.iteration % self.pg_agent_config.checkpoint_freq == 0:
                self.save_model()
                if self.pg_agent_config.save_dir is not None:
                    time_str = str(time.time())
                    self.train_result.to_csv(
                        self.pg_agent_config.save_dir + "/" + time_str + "_train_results_checkpoint.csv")
                    self.eval_result.to_csv(self.pg_agent_config.save_dir + "/" + time_str + "_eval_results_checkpoint.csv")

            if self.pg_agent_config.attacker and self.train_attacker:
                entropy_loss, pg_loss, value_loss, attacker_lr = self.train(self.n_epochs, batch_size=self.batch_size, attacker=True)
                episode_avg_attacker_loss.append(entropy_loss + pg_loss + value_loss)
            if self.pg_agent_config.defender and self.train_defender:
                entropy_loss, pg_loss, value_loss, defender_lr = self.train(self.n_epochs, batch_size=self.batch_size, attacker=False)
                episode_avg_defender_loss.append(entropy_loss + pg_loss + value_loss)

            # If doing alternating optimization and the alternating period is up, change agent that is optimized
            if self.pg_agent_config.alternating_optimization:
                print("optimization_iteration:{}".format(optimization_iteration))
                if self.train_attacker and optimization_iteration > self.pg_agent_config.alternating_period:
                    print("switch training to defender")
                    self.train_attacker = False
                    self.train_defender = True
                    optimization_iteration = 0
                elif self.train_defender and optimization_iteration > self.pg_agent_config.alternating_period:
                    print("switch training to attacker")
                    self.train_attacker = True
                    self.train_defender = False
                    optimization_iteration = 0

        callback.on_training_end()

        return self

    def get_torch_variables(self, attacker:bool = True) -> Tuple[List[str], List[str]]:
        """
        cf base class
        """
        if attacker:
            state_dicts = ["attacker_policy", "attacker_policy.optimizer"]
        else:
            state_dicts = ["defender_policy", "defender_policy.optimizer"]

        return state_dicts, []

    def save_model(self) -> None:
        """
        Saves the PyTorch Model Weights

        :return: None
        """
        time_str = str(time.time())
        if self.pg_agent_config.save_dir is not None:
            if self.pg_agent_config.attacker:
                path = self.pg_agent_config.save_dir + "/" + time_str + "_attacker_policy_network.zip"
                self.pg_agent_config.logger.info("Saving attacker policy-network to: {}".format(path))
                self.save(path, exclude=["tensorboard_writer", "attacker_pool", "defender_pool"])
            if self.pg_agent_config.defender:
                path = self.pg_agent_config.save_dir + "/" + time_str + "_defender_policy_network.zip"
                self.pg_agent_config.logger.info("Saving policy-network to: {}".format(path))
                self.pg_agent_config.logger.info("Saving defender policy-network to: {}".format(path))
                self.save(path, exclude=["tensorboard_writer", "attacker_pool", "defender_pool"])
        else:
            self.pg_agent_config.logger.warning("Save path not defined, not saving policy-networks to disk")
            print("Save path not defined, not saving policy-networks to disk")


    def add_model_to_pool(self, attacker=True) -> None:
        """
        Adds a model to the pool of opponents

        :param attacker: boolean flag indicating whether adding attacker model or defender model
        :return: None
        """
        if self.pg_agent_config.opponent_pool and self.pg_agent_config.opponent_pool_config is not None:
            if attacker:
                model_copy = copy.deepcopy(self.attacker_policy)
                if len(self.attacker_pool) >= self.pg_agent_config.opponent_pool_config.pool_maxsize:
                    self.attacker_pool.pop(0)
                if self.pg_agent_config.opponent_pool_config.quality_scores:
                    if len(self.attacker_pool) == 0:
                        self.attacker_pool.append([model_copy, self.pg_agent_config.opponent_pool_config.initial_quality])
                    elif len(self.attacker_pool) > 0:
                        qualities = self.get_attacker_pool_quality_scores()
                        max_q = max(qualities)
                        self.attacker_pool.append([model_copy, max_q])
                else:
                    self.attacker_pool.append(model_copy)
            else:
                model_copy = copy.deepcopy(self.defender_policy)
                if len(self.defender_pool) >= self.pg_agent_config.opponent_pool_config.pool_maxsize:
                    self.defender_pool.pop(0)
                if self.pg_agent_config.opponent_pool_config.quality_scores:
                    if len(self.defender_pool) == 0:
                        self.defender_pool.append([model_copy, self.pg_agent_config.opponent_pool_config.initial_quality])
                    elif len(self.defender_pool) > 0:
                        qualities = self.get_defender_pool_quality_scores()
                        max_q = max(qualities)
                        self.defender_pool.append([model_copy, max_q])
                else:
                    self.defender_pool.append(model_copy)

    def sample_opponent(self, attacker=True):
        if attacker:
            if self.pg_agent_config.opponent_pool_config.quality_scores:
                quality_scores = self.get_attacker_pool_quality_scores()
                softmax_dist = self.get_softmax_distribution(quality_scores)
                return np.random.choice(list(range(len(self.attacker_pool))), size=1, p=softmax_dist)[0]
            else:
                return np.random.choice(list(range(len(self.attacker_pool))), size=1)[0]
        else:
            if self.pg_agent_config.opponent_pool_config.quality_scores:
                quality_scores = self.get_defender_pool_quality_scores()
                softmax_dist = self.get_softmax_distribution(quality_scores)
                return np.random.choice(list(range(len(self.defender_pool))), size=1, p=softmax_dist)[0]
            else:
                return np.random.choice(list(range(len(self.defender_pool))), size=1)[0]

    def get_attacker_pool_quality_scores(self):
        """
        :return: Returns the quality scores from the attacker pool
        """
        return list(map(lambda x: x[1], self.attacker_pool))

    def get_defender_pool_quality_scores(self):
        """
        :return: Returns the quality scores from the defender pool
        """
        return list(map(lambda x: x[1], self.defender_pool))

    def get_softmax_distribution(self, qualities) -> np.ndarray:
        """
        Converts a list of quality scores into a distribution with softmax

        :param qualities: the list of quality scores
        :return: the softmax distribution
        """
        return softmax(qualities)

    def update_quality_score(self, opponent_idx: int, attacker: bool = True) -> None:
        """
        Updates the quality score of an opponent in the opponent pool. Using same update rule as was used in
        "Dota 2 with Large Scale Deep Reinforcement Learning" by Berner et. al.

        :param opponent_idx: the index of the opponent in the pool
        :param attacker: boolean flag whether attacker or defender pool to be updated
        :return: None
        """
        if attacker:
            N = len(self.attacker_pool)
            qualities = self.get_attacker_pool_quality_scores()
            dist = self.get_softmax_distribution(qualities)
            p = dist[opponent_idx]
            self.attacker_pool[opponent_idx][1] = self.attacker_pool[opponent_idx][1] - \
                                                  (self.pg_agent_config.opponent_pool_config.quality_score_eta / (N * p))
        else:
            N = len(self.defender_pool)
            qualities = self.get_defender_pool_quality_scores()
            dist = self.get_softmax_distribution(qualities)
            p = dist[opponent_idx]
            self.defender_pool[opponent_idx][1] = self.defender_pool[opponent_idx][1] - \
                                                  (self.pg_agent_config.opponent_pool_config.quality_score_eta / (N * p))
