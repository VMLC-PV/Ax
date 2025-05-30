#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict

from time import sleep
from unittest import mock
from unittest.mock import patch, PropertyMock

import numpy as np
from ax.core.arm import Arm
from ax.core.base_trial import (
    MANUAL_GENERATION_METHOD_STR,
    TrialStatus,
    UNKNOWN_GENERATION_METHOD_STR,
)
from ax.core.batch_trial import BatchTrial, GeneratorRunStruct
from ax.core.experiment import Experiment
from ax.core.generator_run import GeneratorRun, GeneratorRunType
from ax.core.parameter import FixedParameter, ParameterType
from ax.core.search_space import SearchSpace
from ax.exceptions.core import UnsupportedError
from ax.runners.synthetic import SyntheticRunner
from ax.utils.common.testutils import TestCase
from ax.utils.testing.core_stubs import (
    get_abandoned_arm,
    get_arm,
    get_arm_weights1,
    get_arms,
    get_experiment,
    get_generator_run,
    get_generator_run2,
    get_weights,
)
from pyre_extensions import assert_is_instance


class BatchTrialTest(TestCase):
    def setUp(self) -> None:
        self.experiment = get_experiment()
        self.experiment.status_quo = None
        self.batch = self.experiment.new_batch_trial()
        arms = get_arms()
        weights = get_weights()
        self.status_quo = arms[0]
        self.sq_weight = weights[0]
        self.new_sq = Arm(parameters={"w": 0.95, "x": 1, "y": "foo", "z": True})
        self.arms = arms[1:]
        self.weights = weights[1:]
        self.batch.add_arms_and_weights(arms=self.arms, weights=self.weights)

    def test__validate_can_attach_data(self) -> None:
        self.batch.mark_running(no_runner_required=True)
        self.batch.mark_completed()

        expected_msg = (
            "Trial 0 already has status 'COMPLETED', so data cannot be attached."
        )
        with self.assertRaisesRegex(UnsupportedError, expected_msg):
            self.batch._validate_can_attach_data()

    def test_Eq(self) -> None:
        new_batch_trial = self.experiment.new_batch_trial()
        self.assertNotEqual(self.batch, new_batch_trial)

        abandoned_arm = get_abandoned_arm()
        abandoned_arm_2 = get_abandoned_arm()
        self.assertEqual(abandoned_arm, abandoned_arm_2)

    def test_BasicProperties(self) -> None:
        self.assertEqual(self.experiment, self.batch.experiment)
        self.assertEqual(self.batch.index, 0)
        self.assertEqual(self.batch.status, TrialStatus.CANDIDATE)
        self.assertIsNotNone(self.batch.time_created)
        self.assertEqual(
            self.batch.generator_run_structs[0].generator_run.generator_run_type,
            GeneratorRunType.MANUAL.name,
        )
        self.assertEqual(self.batch.generation_method_str, MANUAL_GENERATION_METHOD_STR)

        # Test empty trial
        t = self.experiment.new_batch_trial()
        self.assertEqual(len(t.abandoned_arms), 0)
        self.assertEqual(t.generation_method_str, UNKNOWN_GENERATION_METHOD_STR)

    def test_UndefinedSetters(self) -> None:
        with self.assertRaises(NotImplementedError):
            self.batch.arm_weights = get_arm_weights1()

        with self.assertRaises(NotImplementedError):
            self.batch.status = TrialStatus.RUNNING

    def test_BasicSetter(self) -> None:
        self.batch.runner = SyntheticRunner()
        self.assertIsNotNone(self.batch.runner)

        self.batch.trial_type = None
        self.assertIsNone(self.batch.trial_type)

        # Default experiment only supports None as trial_type
        with self.assertRaises(ValueError):
            self.batch.trial_type = ""

    def test_AddArm(self) -> None:
        self.assertEqual(len(self.batch.arms), len(self.arms))
        self.assertEqual(len(self.batch.generator_run_structs), 1)
        self.assertEqual(sum(self.batch.weights), sum(self.weights))

        arm_parameters = get_arm().parameters
        arm_parameters["w"] = 5.0
        self.batch.add_arm(Arm(arm_parameters), 3)

        self.assertEqual(self.batch.arms_by_name["0_2"], self.batch.arms[2])
        self.assertEqual(len(self.batch.arms), len(self.arms) + 1)
        self.assertEqual(len(self.batch.generator_run_structs), 2)
        self.assertEqual(sum(self.batch.weights), sum(self.weights) + 3)

    def test_add_generator_run(self) -> None:
        self.assertEqual(len(self.batch.arms), len(self.arms))
        self.assertEqual(len(self.batch.generator_run_structs), 1)
        self.assertEqual(sum(self.batch.weights), sum(self.weights))

        # Overwrite the GS index to not-None.
        self.batch._generation_step_index = 0

        # one of these arms already exists on the BatchTrial,
        # so we should just update its weight
        new_arms = [
            Arm(parameters={"w": 0.75, "x": 1, "y": "foo", "z": True}),
            Arm(parameters={"w": 1.4, "x": 5, "y": "bar", "z": False}),
        ]
        new_weights = [0.75, 0.25]
        gr = GeneratorRun(arms=new_arms, weights=new_weights)
        self.batch.add_generator_run(gr, 2.0)

        self.assertEqual(len(self.batch.arms), len(self.arms) + 1)
        self.assertEqual(len(self.batch.generator_run_structs), 2)
        self.assertEqual(sum(self.batch.weights), sum(self.weights) + 2)
        # Check the GS index was not overwritten to None.
        self.assertEqual(self.batch._generation_step_index, 0)

    def test_InitWithGeneratorRun(self) -> None:
        generator_run = GeneratorRun(arms=self.arms, weights=self.weights)
        batch = self.experiment.new_batch_trial(generator_run=generator_run)
        batch.add_arms_and_weights(arms=self.arms, weights=self.weights)
        self.assertEqual(self.batch.arms_by_name["0_0"], self.batch.arms[0])
        self.assertEqual(self.batch.arms_by_name["0_1"], self.batch.arms[1])
        self.assertEqual(len(batch.arms), len(self.arms))
        self.assertEqual(len(self.batch.generator_run_structs), 1)

    def test_StatusQuoOverlap(self) -> None:
        # Set status quo to existing arm
        self.batch.set_status_quo_with_weight(self.arms[0], self.sq_weight)
        # Status quo weight is set to the average of other arms' weights.
        # In this case, there are only two arms: 0_0 (SQ) and 0_1 (not SQ).
        # So their weights are equal, as weight(0_0) = avg(weight(0_1)).
        self.assertEqual(self.batch.weights[0], self.batch.weights[1])
        self.assertTrue(self.batch.status_quo.parameters == self.arms[0].parameters)
        self.assertEqual(self.batch.status_quo.name, self.batch.arms[0].name)
        self.assertEqual(self.batch.arm_weights[self.batch.arms[0]], self.sq_weight)
        self.assertEqual(sum(self.batch.weights), self.weights[1] + self.sq_weight)

        # Set status quo to new arm, add it
        self.batch.set_status_quo_with_weight(self.new_sq, self.sq_weight)
        self.assertEqual(self.batch.status_quo.name, "status_quo_0")
        self.batch.add_arms_and_weights([self.new_sq])
        self.assertEqual(
            self.batch.generator_run_structs[1].generator_run.arms[0].name,
            "status_quo_0",
        )

    def test_status_quo_cannot_have_negative_weight(self) -> None:
        with self.assertRaises(ValueError):
            self.batch.set_status_quo_with_weight(self.new_sq, -1)

    def test_status_quo_cannot_be_set_directly(self) -> None:
        # Test that directly setting the status quo raises an error
        with self.assertRaises(NotImplementedError):
            self.batch.status_quo = self.new_sq

    def test_status_quo_can_be_set_to_a_new_arm(self) -> None:
        tot_weight = sum(self.batch.weights)
        # Set status quo to new arm
        self.batch.set_status_quo_with_weight(self.new_sq, self.sq_weight)
        self.assertTrue(self.batch.status_quo == self.new_sq)
        self.assertEqual(self.batch.status_quo.name, "status_quo_0")
        self.assertEqual(sum(self.batch.weights), tot_weight + self.sq_weight)

    def test_status_quo_weight_is_ignored_when_none(self) -> None:
        tot_weight = sum(self.batch.weights)
        # sq weight should be ignored when sq is None
        self.batch.unset_status_quo()
        self.assertEqual(sum(self.batch.weights), tot_weight)
        self.assertIsNone(self.batch.status_quo)
        self.assertIsNone(self.batch._status_quo_weight_override)

    def test_status_quo_set_on_clone(
        self,
    ) -> None:
        self.experiment.status_quo = self.status_quo
        batch2 = self.batch.clone()
        self.assertEqual(batch2.status_quo, self.experiment.status_quo)
        # Since add_status_quo_arm was False,
        # _status_quo_weight_override should be False and the
        # status_quo arm should not appear in arm_weights
        self.assertIsNone(batch2._status_quo_weight_override)
        self.assertTrue(batch2.status_quo not in batch2.arm_weights)
        self.assertEqual(sum(batch2.weights), sum(self.weights))
        # Test with add_status_quo_arm=True
        batch3 = self.experiment.new_batch_trial(add_status_quo_arm=True)
        self.assertEqual(batch3._status_quo_weight_override, 1.0)
        self.assertTrue(batch2.status_quo in batch3.arm_weights)

    def test_status_quo_cannot_be_set_with_different_name(self) -> None:
        # Set status quo to new arm
        self.batch.set_status_quo_with_weight(self.status_quo, self.sq_weight)
        with self.assertRaises(ValueError):
            self.batch.set_status_quo_with_weight(
                Arm(self.status_quo.parameters, name="new_name"), 1
            )

    def test_cannot_set_status_quo_with_weight_without_status_quo(self) -> None:
        self.experiment.status_quo = None
        with self.assertRaises(ValueError):
            self.experiment.new_batch_trial(add_status_quo_arm=True)

    def test_ArmsByName(self) -> None:
        # Initializes empty
        newbatch = self.experiment.new_batch_trial()
        self.assertEqual(newbatch.arms_by_name, {})

        # Correct when arms have been added
        self.assertEqual(self.batch.arms_by_name["0_0"], self.batch.arms[0])
        self.assertEqual(self.batch.arms_by_name["0_1"], self.batch.arms[1])

        # Refreshed when arm is added
        newbatch.add_arm(self.batch.arms[0])
        self.assertEqual(newbatch.arms_by_name, {"0_0": self.batch.arms[0]})

        # Refreshed when status quo is set
        newbatch.set_status_quo_with_weight(self.batch.arms[1], 1.0)
        self.assertEqual(
            newbatch.arms_by_name,
            {"0_0": self.batch.arms[0], "0_1": self.batch.arms[1]},
        )

        # Refreshed when status quo is unset
        newbatch.unset_status_quo()
        self.assertEqual(
            newbatch.arms_by_name,
            {"0_0": self.batch.arms[0]},
        )

    def test_BatchLifecycle(self) -> None:
        # Check that state of trial statuses mapping on experiment: there should only be
        # one index, 0, among the `CANDIDATE` trials.
        trial_idcs_by_status = iter(self.experiment.trial_indices_by_status.values())
        self.assertEqual(next(trial_idcs_by_status), {0})  # `CANDIDATE` trial indices
        # ALl other trial statuses should not yet have trials carry them.
        self.assertTrue(all(len(idcs) == 0 for idcs in trial_idcs_by_status))
        staging_mock = PropertyMock()
        with patch.object(SyntheticRunner, "staging_required", staging_mock):
            mock_runner = SyntheticRunner()
            staging_mock.return_value = True
            self.batch.runner = mock_runner
            self.batch.run()
            self.assertEqual(self.batch.status, TrialStatus.STAGED)
            # Check that the trial statuses mapping on experiment has been updated.
            self.assertEqual(
                self.experiment.trial_indices_by_status[TrialStatus.STAGED], {0}
            )
            self.assertTrue(
                # pyre-fixme[6]: For 1st param expected `Iterable[object]` but got
                #  `bool`.
                all(len(idcs) == 0)
                for status, idcs in self.experiment.trial_indices_by_status.items()
                if status != TrialStatus.STAGED
            )
            self.assertIsNotNone(self.batch.time_staged)
            self.assertTrue(self.batch.status.is_deployed)
            self.assertFalse(self.batch.status.expecting_data)

            # Cannot change arms or runner once run
            with self.assertRaises(ValueError):
                self.batch.add_arms_and_weights(arms=self.arms, weights=self.weights)

            with self.assertRaises(ValueError):
                self.batch.runner = None

            # Cannot run batch that was already run
            with self.assertRaises(ValueError):
                self.batch.run()

            self.batch.mark_running()
            self.assertEqual(self.batch.status, TrialStatus.RUNNING)
            # Check that the trial statuses mapping on experiment has been updated.
            self.assertEqual(
                self.experiment.trial_indices_by_status[TrialStatus.RUNNING], {0}
            )
            self.assertTrue(
                # pyre-fixme[6]: For 1st param expected `Iterable[object]` but got
                #  `bool`.
                all(len(idcs) == 0)
                for status, idcs in self.experiment.trial_indices_by_status.items()
                if status != TrialStatus.RUNNING
            )
            self.assertIsNotNone(self.batch.time_run_started)
            self.assertTrue(self.batch.status.expecting_data)

            self.batch.complete()
            # Cannot complete that which is already completed
            with self.assertRaises(ValueError):
                self.batch.complete()

            # Verify trial is completed
            self.assertEqual(self.batch.status, TrialStatus.COMPLETED)
            # Check that the trial statuses mapping on experiment has been updated.
            self.assertEqual(
                self.experiment.trial_indices_by_status[TrialStatus.COMPLETED], {0}
            )
            self.assertTrue(
                # pyre-fixme[6]: For 1st param expected `Iterable[object]` but got
                #  `bool`.
                all(len(idcs) == 0)
                for status, idcs in self.experiment.trial_indices_by_status.items()
                if status != TrialStatus.COMPLETED
            )
            self.assertIsNotNone(self.batch.time_completed)
            self.assertTrue(self.batch.status.is_terminal)

            # Cannot change status after BatchTrial is completed
            with self.assertRaises(ValueError):
                self.batch.mark_staged()

            with self.assertRaises(ValueError):
                self.batch.mark_completed()

            with self.assertRaises(ValueError):
                self.batch.mark_running()

            with self.assertRaises(ValueError):
                self.batch.mark_abandoned()

            with self.assertRaises(ValueError):
                self.batch.mark_failed()

            # Check that the trial statuses mapping on experiment is updated when
            # trial status is set hackily / directly, without using `mark_X`.
            self.batch._status = TrialStatus.CANDIDATE
            self.assertEqual(
                self.experiment.trial_indices_by_status[TrialStatus.CANDIDATE], {0}
            )
            self.assertTrue(
                # pyre-fixme[6]: For 1st param expected `Iterable[object]` but got
                #  `bool`.
                all(len(idcs) == 0)
                for status, idcs in self.experiment.trial_indices_by_status.items()
                if status != TrialStatus.CANDIDATE
            )

    def test_AbandonBatchTrial(self) -> None:
        reason = "BatchTrial behaved poorly"
        self.batch.mark_abandoned(reason)

        self.assertEqual(self.batch.status, TrialStatus.ABANDONED)
        self.assertIsNotNone(self.batch.time_completed)
        self.assertEqual(self.batch.abandoned_reason, reason)

    def test_FailedBatchTrial(self) -> None:
        self.batch.runner = SyntheticRunner()
        self.batch.run()
        self.batch.mark_failed()

        self.assertEqual(self.batch.status, TrialStatus.FAILED)
        self.assertIsNotNone(self.batch.time_completed)

    def test_EarlyStoppedBatchTrial(self) -> None:
        self.batch.runner = SyntheticRunner()
        self.batch.run()
        self.batch.attach_batch_trial_data(
            raw_data={
                self.batch.arms[0].name: {"m1": 1.0, "m2": 2.0},
                self.batch.arms[1].name: {"m1": 3.0, "m2": 4.0},
            }
        )
        self.batch.mark_early_stopped()

        self.assertEqual(self.batch.status, TrialStatus.EARLY_STOPPED)
        self.assertIsNotNone(self.batch.time_completed)

    def test_AbandonArm(self) -> None:
        arm = self.batch.arms[0]
        reason = "Bad arm"
        self.batch.mark_arm_abandoned(arm.name, reason)
        self.assertEqual(len(self.batch.abandoned_arms), 1)
        self.assertEqual(self.batch.abandoned_arms[0], arm)

        self.assertEqual(len(self.batch.abandoned_arms_metadata), 1)
        metadata = self.batch.abandoned_arms_metadata[0]
        self.assertEqual(metadata.reason, reason)
        self.assertEqual(metadata.name, arm.name)
        self.assertEqual(len(self.batch.active_arms), len(self.arms) - 1)
        self.assertNotIn(arm, self.batch.active_arms)

        # Fail to abandon arm not in BatchTrial
        with self.assertRaises(ValueError):
            self.batch.mark_arm_abandoned(
                Arm(parameters={"x": 3, "y": "fooz", "z": False})
            )

    @mock.patch(
        f"{get_experiment.__module__}.Experiment.supports_trial_type",
        return_value=True,
    )
    def test_clone_to(self, _) -> None:
        experiment = get_experiment()
        experiment.status_quo = None
        batch = experiment.new_batch_trial(ttl_seconds=123, trial_type="foo")
        arms = get_arms()
        weights = get_weights()
        status_quo = arms[0]
        arms = arms[1:]
        weights = weights[1:]
        batch.add_arms_and_weights(arms=arms, weights=weights)
        batch.update_run_metadata(metadata={"foo": "bar"})
        batch.update_stop_metadata(metadata={"bar": "baz"})

        # cloning the trial and attached it to the same experiment
        new_batch_trial_0 = batch.clone_to()
        # cloning the trial and attached it to a new experiment
        new_experiment = get_experiment()
        new_experiment.status_quo = None
        batch.clone_to(new_experiment)
        new_batch_trial_1 = assert_is_instance(new_experiment.trials[0], BatchTrial)

        self.assertEqual(new_batch_trial_0.index, 1)
        # Set index to original trial's value for equality check.
        new_batch_trial_0._index = batch.index
        new_batch_trial_0._time_created = batch._time_created
        new_batch_trial_1._time_created = batch._time_created
        self.assertEqual(new_batch_trial_0, batch)
        self.assertEqual(new_batch_trial_1, batch)

        # check that trial_type is cloned correctly
        self.assertEqual(new_batch_trial_0.trial_type, "foo")

        # make sure modifying the cloned batch trial does not affect original one
        new_batch_trial_1.add_arm(
            Arm(name="new_arm", parameters={"w": 2.6, "x": 2, "y": "baz", "z": True})
        )
        self.assertEqual(len(new_batch_trial_1.arms), 3)
        self.assertEqual(len(batch.arms), 2)

        # cloning a trial that has status quo arm
        status_quo = Arm(
            name="status_quo", parameters={"w": 0.0, "x": 1, "y": "foo", "z": True}
        )
        batch.set_status_quo_with_weight(status_quo=status_quo, weight=1.0)
        batch.mark_running(no_runner_required=True)
        new_batch_trial = batch.clone_to()
        self.assertEqual(new_batch_trial.index, 2)
        # Set index & time_created to original trial's value for equality check.
        new_batch_trial._index = batch.index
        new_batch_trial._time_created = batch._time_created
        self.assertEqual(new_batch_trial, batch)
        # test cloning with clear_trial_type=True
        new_batch_trial = batch.clone_to(clear_trial_type=True)
        self.assertIsNone(new_batch_trial.trial_type)
        self.assertEqual(
            new_batch_trial.generation_method_str, MANUAL_GENERATION_METHOD_STR
        )

    def test_Runner(self) -> None:
        # Verify BatchTrial without runner will fail
        with self.assertRaises(ValueError):
            self.batch.run()

        # Verify mark running without runner will fail
        with self.assertRaises(ValueError):
            self.batch.mark_running()

        self.batch.runner = SyntheticRunner()
        self.batch.run()
        self.assertEqual(self.batch.deployed_name, "test_0")
        self.assertNotEqual(len(self.batch.run_metadata.keys()), 0)
        self.assertEqual(self.batch.status, TrialStatus.RUNNING)

        # Verify setting runner on experiment but not BatchTrial
        # Also mock staging_required to be false
        staging_mock = PropertyMock()
        with patch.object(SyntheticRunner, "staging_required", staging_mock):
            mock_runner = SyntheticRunner()
            staging_mock.return_value = True

            self.experiment.runner = mock_runner
            b2 = self.experiment.new_batch_trial()
            b2.run()
            self.assertEqual(b2.deployed_name, "test_1")
            self.assertEqual(b2.status, TrialStatus.STAGED)

    def test_IsFactorial(self) -> None:
        self.assertFalse(self.batch.is_factorial)

        # Insufficient factors
        small_experiment = Experiment(
            name="small_test",
            search_space=SearchSpace([FixedParameter("a", ParameterType.INT, 4)]),
        )
        small_trial = small_experiment.new_batch_trial().add_arm(Arm({"a": 4}))
        self.assertFalse(small_trial.is_factorial)

        new_batch_trial = self.experiment.new_batch_trial()
        new_batch_trial.add_arms_and_weights(
            arms=[
                Arm(parameters={"w": 0.75, "x": 1, "y": "foo", "z": True}),
                Arm(parameters={"w": 0.75, "x": 2, "y": "foo", "z": True}),
                Arm(parameters={"w": 0.77, "x": 1, "y": "foo", "z": True}),
            ]
        )
        self.assertFalse(new_batch_trial.is_factorial)

        new_batch_trial = self.experiment.new_batch_trial()
        new_batch_trial.add_arms_and_weights(
            arms=[
                Arm(parameters={"w": 0.77, "x": 1, "y": "foo", "z": True}),
                Arm(parameters={"w": 0.77, "x": 2, "y": "foo", "z": True}),
                Arm(parameters={"w": 0.75, "x": 1, "y": "foo", "z": True}),
                Arm(parameters={"w": 0.75, "x": 2, "y": "foo", "z": True}),
            ]
        )
        self.assertTrue(new_batch_trial.is_factorial)

    def test_NormalizedArmWeights(self) -> None:
        new_batch_trial = self.experiment.new_batch_trial()
        parameterizations = [
            {"w": 0.75, "x": 1, "y": "foo", "z": True},
            {"w": 0.77, "x": 2, "y": "foo", "z": True},
        ]
        arms = [Arm(parameters=p) for i, p in enumerate(parameterizations)]
        new_batch_trial.add_arms_and_weights(arms=arms, weights=[2, 1])

        # test normalizing to 1
        arm_weights = new_batch_trial.normalized_arm_weights()
        # self.assertEqual(list(arm_weights.keys()), arms)
        batch_arm_parameters = [arm.parameters for arm in list(arm_weights.keys())]
        arm_parameters = [arm.parameters for arm in arms]
        self.assertEqual(batch_arm_parameters, arm_parameters)
        self.assertTrue(np.allclose(list(arm_weights.values()), [2 / 3, 1 / 3]))

        # test normalizing to 100
        arm_weights = new_batch_trial.normalized_arm_weights(total=100)
        batch_arm_parameters = [arm.parameters for arm in list(arm_weights.keys())]
        arm_parameters = [arm.parameters for arm in arms]
        self.assertEqual(batch_arm_parameters, arm_parameters)
        self.assertTrue(np.allclose(list(arm_weights.values()), [200 / 3, 100 / 3]))

        # test normalizing with truncation
        arm_weights = new_batch_trial.normalized_arm_weights(total=1, trunc_digits=2)
        batch_arm_parameters = [arm.parameters for arm in list(arm_weights.keys())]
        arm_parameters = [arm.parameters for arm in arms]
        self.assertEqual(batch_arm_parameters, arm_parameters)
        self.assertTrue(np.allclose(list(arm_weights.values()), [0.67, 0.33]))

    def test_AddGeneratorRunValidation(self) -> None:
        new_batch_trial = self.experiment.new_batch_trial()
        new_arms = [
            Arm(name="0_1", parameters={"w": 0.75, "x": 1, "y": "foo", "z": True}),
            Arm(name="0_2", parameters={"w": 0.75, "x": 1, "y": "foo", "z": True}),
        ]
        gr = GeneratorRun(arms=new_arms)
        with self.assertRaises(ValueError):
            new_batch_trial.add_generator_run(gr)

    def test_Repr(self) -> None:
        self.assertEqual(
            str(self.batch),
            "BatchTrial(experiment_name='test', index=0, status=TrialStatus.CANDIDATE)",
        )

    def test_TTL(self) -> None:
        # Verify that TLL is checked on execution of the `status` property.
        self.batch.ttl_seconds = 1
        self.batch.mark_running(no_runner_required=True)
        self.assertTrue(self.batch.status.is_running)
        sleep(1)  # Wait 1 second for trial TTL to elapse.
        self.assertTrue(self.batch.status.is_failed)
        self.assertIn(0, self.experiment.trial_indices_by_status[TrialStatus.FAILED])

        # Verify that TTL is checked on `experiment.trial_indices_by_status`.
        batch_trial = self.experiment.new_batch_trial(ttl_seconds=1)
        batch_trial.mark_running(no_runner_required=True)
        self.assertTrue(batch_trial.status.is_running)
        sleep(1)  # Wait 1 second for trial TTL to elapse.
        self.assertIn(1, self.experiment.trial_indices_by_status[TrialStatus.FAILED])
        self.assertTrue(self.experiment.trials[1].status.is_failed)

        # Verify that TTL is checked on `experiment.trials`.
        batch_trial = self.experiment.new_batch_trial(ttl_seconds=1)
        batch_trial.mark_running(no_runner_required=True)
        self.assertTrue(batch_trial.status.is_running)
        self.assertIn(2, self.experiment.trial_indices_by_status[TrialStatus.RUNNING])
        sleep(1)  # Wait 1 second for trial TTL to elapse.
        self.experiment.trials
        # Check `_status`, not `status`, to ensure it's within `trials` that the status
        # was actually changed, not in `status`.
        self.assertEqual(batch_trial._status, TrialStatus.FAILED)
        self.assertIn(2, self.experiment.trial_indices_by_status[TrialStatus.FAILED])

    def test_get_candidate_metadata_from_all_generator_runs(self) -> None:
        self.assertEqual(self.batch.generation_method_str, MANUAL_GENERATION_METHOD_STR)
        gr_1 = get_generator_run()
        gr_2 = get_generator_run2()
        self.batch.add_generator_run(gr_1)
        self.assertEqual(self.batch.generation_method_str, "Manual, Sobol")

        # Arms are named when adding GR to trial, so reassign to have a GR that has
        # names arms.
        gr_1 = self.batch._generator_run_structs[-1].generator_run
        self.batch.add_generator_run(gr_2)
        gr_2 = self.batch._generator_run_structs[-1].generator_run
        # gr_2 has no candidate metadata; all candidate metadata should come from gr_1
        cand_metadata_expected = {
            a.name: gr_1.candidate_metadata_by_arm_signature[a.signature]
            for a in gr_1.arms
        }
        self.assertEqual(
            self.batch._get_candidate_metadata_from_all_generator_runs(),
            cand_metadata_expected,
        )
        for arm in self.batch.arms:
            self.assertEqual(
                cand_metadata_expected[arm.name],
                self.batch._get_candidate_metadata(arm.name),
            )
        self.assertRaises(
            ValueError, self.batch._get_candidate_metadata, "this_is_not_an_arm"
        )

        # Check that if we add cand. metadata to gr_2, it will appear in cand.
        # metadata for the batch.
        gr_3 = get_generator_run2()
        new_cand_metadata = {
            a.signature: {"md_key": f"md_val_{a.signature}"} for a in gr_3.arms
        }
        # pyre-fixme[8]: Attribute has type `Optional[Dict[str, Optional[Dict[str,
        #  typing.Any]]]]`; used as `Dict[str, Dict[str, str]]`.
        gr_3._candidate_metadata_by_arm_signature = new_cand_metadata
        self.batch.add_generator_run(gr_3)
        gr_3 = self.batch._generator_run_structs[-1].generator_run
        cand_metadata_expected.update(
            {
                a.name: gr_1.candidate_metadata_by_arm_signature[a.signature]
                for a in gr_1.arms
            }
        )
        self.assertEqual(
            self.batch._get_candidate_metadata_from_all_generator_runs(),
            cand_metadata_expected,
        )
        for arm in self.batch.arms:
            self.assertEqual(
                cand_metadata_expected[arm.name],
                self.batch._get_candidate_metadata(arm.name),
            )
        self.assertEqual(self.batch.generation_method_str, "Manual, Sobol")

    def test_Sortable(self) -> None:
        new_batch_trial = self.experiment.new_batch_trial()
        self.assertTrue(self.batch < new_batch_trial)

        abandoned_arm = get_abandoned_arm()
        abandoned_arm_2 = get_abandoned_arm()
        abandoned_arm_2.name = "0_1"
        self.assertTrue(abandoned_arm < abandoned_arm_2)

        generator_run = get_generator_run()
        generator_run_struct = GeneratorRunStruct(
            generator_run=generator_run, weight=1.0
        )
        generator_run_struct_2 = GeneratorRunStruct(
            generator_run=generator_run, weight=2.0
        )
        self.assertTrue(generator_run_struct < generator_run_struct_2)

    def test_attach_batch_trial_data(self) -> None:
        # Verify components before we attach trial data
        self.assertEqual(2, len(self.batch.arms))
        arm1_name = self.batch.arms[0].name
        arm2_name = self.batch.arms[1].name

        self.assertEqual(
            2,
            len(self.batch.experiment.metrics)
            - len(self.batch.experiment.tracking_metrics),
        )
        self.assertTrue("m1" in self.batch.experiment.metrics)
        self.assertTrue("m2" in self.batch.experiment.metrics)

        data = self.batch.lookup_data().df.to_dict(orient="index")
        self.assertTrue(len(data) == 0)

        # Attach data
        self.batch.attach_batch_trial_data(
            raw_data={
                arm1_name: {"m1": 1.0, "m2": 2.0},
                arm2_name: {"m1": 3.0, "m2": 4.0},
            }
        )

        # Confirm the expected state after attaching data
        data = (
            self.batch.lookup_data()
            .df.set_index(["arm_name", "metric_name"])
            .to_dict(orient="index")
        )

        self.assertEqual(1.0, data[(arm1_name, "m1")]["mean"])
        self.assertEqual(2.0, data[(arm1_name, "m2")]["mean"])
        self.assertEqual(3.0, data[(arm2_name, "m1")]["mean"])
        self.assertEqual(4.0, data[(arm2_name, "m2")]["mean"])
