import argparse

from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import make_scorer, f1_score, roc_auc_score, \
    accuracy_score
from sklearn.model_selection import cross_validate, StratifiedKFold
from sklearn.pipeline import Pipeline

from core.data_manager import DataManager
import os
import numpy as np
import matplotlib.pyplot as ply
from utils.dslab_logging import get_logger
from utils.output_class import Output
from utils.set_seed import SetSeed
from utils.enums import Classifier, Task, Metric, DataType
from preprocessing.dataset import DatapointKey as DK

# Set up logger and seed
logger = get_logger()
SetSeed().set_seed()


class HistogramTransformer(BaseEstimator, TransformerMixin):
    def __init__(self, n_bins=100):
        """
        Transformer that produces histogram features

        Parameters
        ----------
            n_bins: int
                number of bins to use in the histograms
        """
        self.n_bins = n_bins

    def _histograms_for_variable(self, array, n_bins):
        """
        Computes the histograms for array of shape (n_samples, n_days)

        Parameters
        ----------
            array: np.array
                array of shape (n_samples, n_days)
            n_bins: int
                number of bins to use in the histograms
        """
        # Compute the histogram range over all given samples in array
        hist_range = [np.min(array), np.max(array)]

        histograms = np.apply_along_axis(
            lambda a: np.histogram(a, bins=n_bins, range=hist_range)[0],
            axis=1,
            arr=array)
        return histograms

    def _histograms(self, data, n_bins=100):
        """
        Creates histogram features for given variables.
        For each variable, we create a histogram with n_bins.

        Parameters
        ----------
            data: numpy.array
                contains the preprocessed data
                of shape (num_data, num_variables, num_days)
            n_bins: int
                number of bins to use in the histograms

        """
        n_features = data.shape[1]
        # For each feature variable, compute the histograms for all samples
        result = np.array([
            self._histograms_for_variable(data[:, i, :], n_bins=n_bins)
            for i in range(n_features)
        ])
        # result has shape (n_features, n_samples, n_bins)
        # We reshape it to have the shape (n_samples, n_features, n_bins)
        result = np.swapaxes(result, 0, 1)
        return result

    def transform(self, X, y=None, **fit_params):
        """
        Extracts histogram features from X.

        Parameters
        ----------
            X: np.array
                array of shape (n_samples, n_features, n_days)
            y: None
        """

        histogram_features = self._histograms(X, n_bins=self.n_bins)
        # We need to reshape the array of shape (n_samples, n_features, n_bins)
        # to (n_samples, n_features * n_bins)
        X = np.reshape(histogram_features, (histogram_features.shape[0], -1))
        return X

    def fit(self, X, y=None, **fit_params):
        # No fitting required
        return self


class RandomForestClassification():
    """A class that receives as input the processed data and the definition that
    you want prediction for and does prediction using the RandomForest
    Classifier.
    """

    def __init__(self, definition, path_train, n_bins=100, n_estimators=100,
                 cv_folds=5, path_test=None):
        """
        Parameters
        ----------
            definition: string
                the definition that you want to do classification for
                e.g. "CP07"
            path_train: string
                the path where the preprocessed input data are
            n_bins: int
                number of bins to use for the histogram extraction
            n_estimators: int
                number of estimators to use in the RandomForestClassifier

        """
        self.data_manager_train = DataManager(path_train)
        if path_test:
            self.data_manager_test = DataManager(path_test)

        self.definition = definition

        self.cv_folds = cv_folds
        self.n_bins = n_bins

        self.metric_txt = ["F1", "ROCAUC", "Accuracy"]
        self.metrics = [f1_score, roc_auc_score, accuracy_score]
        self.classifier = RandomForestClassifier(
            n_estimators=n_estimators,
            n_jobs=4
        )

    def _get_raw_data(self, train=True):
        variables = [DK.TEMP_60_90, DK.WIND_60, DK.WIND_65]
        if train:
            data = self.data_manager_train.get_data_for_variables(variables)
        else:
            data = self.data_manager_test.get_data_for_variables(variables)
        return data

    def _get_labels(self, train=True):
        if train:
            raw_labels = self.data_manager_train.get_data_for_variable(
                self.definition)
        else:
            raw_labels = self.data_manager_test.get_data_for_variable(
                self.definition)
        binary_label = np.apply_along_axis(lambda a: 1 if 1 in a else 0,
                                           axis=1, arr=raw_labels)
        return binary_label

    def _get_pipeline(self):
        # We create a pipeline in order to apply the same independent
        # preprocessing steps to all folds in the cross-validation
        feature_extraction = HistogramTransformer(n_bins=self.n_bins)
        steps = [
            ('feature_extraction', feature_extraction),
            ('model', self.classifier)
        ]
        return Pipeline(steps)

    def evaluate_simulated(self, plot=False):
        """
        Trains the model in a 5-fold cross validation and returns a mean
        and standard deviation for each metric used for evaluation.

         Parameters
        ----------
            plot: bool
                Whether to show a plot or not.

        Returns
        -------
            mean_scores: list of length len(self.metrics),
            std_scores: list of length len(self.metrics)
        """
        logger.info("Evaluating simulated data...")
        pipeline = self._get_pipeline()
        # Get the raw data for the features
        logger.info("Loading data...")
        raw_data_train = self._get_raw_data(train=True)
        # Bring labels in correct format
        labels_train = self._get_labels()

        # We have an unbalanced dataset, so we stratify
        cv = StratifiedKFold(self.cv_folds, shuffle=True)

        logger.info("Scoring for {} metrics...".format(len(self.metrics)))
        # Produce scores for all scoring metrics
        scorers = {txt: make_scorer(metric) for txt, metric in
                   zip(self.metric_txt, self.metrics)}
        scores = cross_validate(pipeline, raw_data_train, labels_train, cv=cv,
                                scoring=scorers)
        # cross_validate returns dict.
        # Extract test scores
        scores = [scores['test_{}'.format(txt)] for txt in self.metric_txt]

        # We only want to keep mean and std for each metric
        scores_means = [np.mean(score) for score in scores]
        scores_std = [np.std(score) for score in scores]
        for i, metric in enumerate(self.metrics):
            logger.info("{} score (mean / std): {} / {}".format(
                self.metric_txt[i],
                scores_means[i],
                scores_std[i])
            )

        if plot:
            self.plot(scores_means, scores_std)

        # Write results to output file
        output_default_args = dict(
            classifier=Classifier.randomforest,
            task=Task.classification,
            data_type=DataType.simulated,
            definition=self.definition
        )

        out_metrics = (
            (Metric.f1, scores[0]),
            (Metric.auroc, scores[1]),
            (Metric.accuracy, scores[2])
        )
        for metric, score in out_metrics:
            Output(
                **output_default_args,
                metric=metric,
                scores=score
            ).write_output()

        return scores_means, scores_std

    def evaluate_real(self):
        """
        Trains the model on the entire simulated training set and
        predicts the real data. Reports a single score for all used metrics.

        Returns
        -------
            scores: list of length len(self.metrics)
        """
        logger.info("Evaluating real data...")
        pipeline = self._get_pipeline()
        # Get the raw data for the features
        raw_data_train = self._get_raw_data()
        # Bring labels in correct format
        labels_train = self._get_labels()

        # We fit our classifier on all the simulated data
        pipeline.fit(raw_data_train, labels_train)

        logger.info("Loading test data...")
        raw_data_test = self._get_raw_data(train=False)
        labels_test = self._get_labels(train=False)
        logger.info("Evaluating on real data ({} data points)...".format(
            len(labels_test)))

        # Predict real test dataset and evaulate
        pred_test = pipeline.predict(raw_data_test)

        scores = [metric(labels_test, pred_test) for metric in self.metrics]
        logger.info("Scores ({}, {}, {}): {}, {}, {}".format(*self.metric_txt,
                                                             *scores))

        # Write results to output file
        output_default_args = dict(
            classifier=Classifier.randomforest,
            task=Task.classification,
            data_type=DataType.real,
            definition=self.definition
        )

        out_metrics = (
            (Metric.f1, scores[0]),
            (Metric.auroc, scores[1]),
            (Metric.accuracy, scores[2])
        )
        for metric, score in out_metrics:
            Output(
                **output_default_args,
                metric=metric,
                scores=[score]
            ).write_output()

        return scores

    def plot(self, scores_mean, scores_std):
        """
        Creates a bar plot with error bars for given means and std.
        One bar corresponds to one metric.
        For the error bars, use 2*std.
        At a later stage, we can use bootstrapping to estimate a correct
        confidence interval.

        Parameters
        ----------
            scores_mean: list of length len(self.metrics) with scores
            for each metric.
            scores_std: list of length len(self.metrics) with
            standard deviations for each metric
        """
        ply.figure()
        ply.bar(self.metric_txt, scores_mean, yerr=scores_std, align='center',
                alpha=0.5, ecolor='black', capsize=10)

        ply.title(
            "Results {}".format(self.__class__.__name__))

        ply.ylim(0, 1)
        ply.show()


def run_gridsearch():
    # Run gridsearch for n_bins
    print("n_bins --> scores for F1, ROCAUC, Accuracy")
    for n_bins in [5, 10, 20, 30, 50, 80, 120, 150, 200]:
        model = RandomForestClassification(
            definition=args.definition,
            path_train=args.input_path_train,
            n_bins=n_bins,
            n_estimators=1000
        )

        mean_scores, _ = model.evaluate_simulated(plot=False)
        print("n_bins: {} --> {:.4f}, {:.4f}, {:.4f}".format(n_bins,
                                                             *mean_scores)
              )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A simple prediction \
            scheme using histogram features and RandomForest')
    parser.add_argument(
        "-d",
        "--definition",
        choices=(DK.CP07, DK.UT, DK.U65),
        help="Choose the definition that you want to run classification",
        action="store",
        default=DK.CP07
    )
    parser.add_argument(
        "-i_train",
        "--input_path_train",
        help="Choose the input relative path where the data are",
        action="store",
        default=os.getenv("DSLAB_CLIMATE_LABELED_DATA")
    )
    parser.add_argument(
        "-i_test",
        "--input_path_test",
        help="Choose the input relative path where the data are",
        action="store",
        default=os.getenv("DSLAB_CLIMATE_LABELED_REAL_DATA")
    )
    parser.add_argument(
        "-ds",
        "--dataset",
        choices=("simulated", "real"),
        help="Choose the input dataset",
        action="store",
        default="simulated"
    )
    args = parser.parse_args()

    # n_bins was estimated via cross-validation on simulated data set
    n_bins = 20
    n_estimators = 1000

    if args.dataset == "real":
        model = RandomForestClassification(
            definition=args.definition,
            path_train=args.input_path_train,
            n_bins=n_bins,
            n_estimators=n_estimators,
            path_test=args.input_path_test
        )
        scores = model.evaluate_real()
    else:
        model = RandomForestClassification(
            definition=args.definition,
            path_train=args.input_path_train,
            n_bins=n_bins,
            n_estimators=n_estimators
        )
        model.evaluate_simulated()
