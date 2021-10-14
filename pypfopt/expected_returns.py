"""
The ``expected_returns`` module provides functions for estimating the expected returns of
the assets, which is a required input in mean-variance optimization.

By convention, the output of these methods is expected *annual* returns. It is assumed that
*daily* prices are provided, though in reality the functions are agnostic
to the time period (just change the ``frequency`` parameter). Asset prices must be given as
a pandas dataframe, as per the format described in the :ref:`user-guide`.

All of the functions process the price data into percentage returns data, before
calculating their respective estimates of expected returns.

Currently implemented:

    - general return model function, allowing you to run any return model from one function.
    - mean historical return
    - exponentially weighted mean historical return
    - CAPM estimate of returns

Additionally, we provide utility functions to convert from returns to prices and vice-versa.
"""

import warnings
import pandas as pd
import numpy as np
import pyspark
from pyspark.sql import functions as F
from pyspark.sql.window import Window


def returns_from_prices(prices, log_returns=False, is_spark=False):
    """
    Calculate the returns given prices.

    :param prices: adjusted (daily) closing prices of the asset, each row is a
                   date and each column is a ticker/id.
    :type prices: pd.DataFrame or spark.DataFrame with a "date_index" col of type Timestamp
    :param log_returns: whether to compute using log returns
    :type log_returns: bool, defaults to False
    :is_spark: whether prices is a spark dataframe
    :type is_spark: bool, optional
    :return: (daily) returns
    :rtype: pd.DataFrame or spark dataframe if is_spark is true
    """
    if is_spark is True and type(prices) != pyspark.sql.dataframe.DataFrame:
        raise RuntimeError("Loading a non-spark dataframe to a spark session is not supported!")
        sys.exit(1)
    if is_spark is True and type(prices) == pyspark.sql.dataframe.DataFrame and "date_index" not in prices.columns:
        raise RuntimeError("Loading a spark dataframe without a 'date_index' column is not supported!")
        sys.exit(1)

    if log_returns and not is_spark:
        return np.log(1 + prices.pct_change()).dropna(how="all")
    if not log_returns and not is_spark:
        return prices.pct_change().dropna(how="all")

    if log_returns and is_spark:
        price_cols = prices.columns
        price_cols.remove("date_index")
        for col in price_cols:
            prices = prices.\
                withColumn("tmp_lag_1", F.lag(prices[col]) \
                                             .over(Window.orderBy("date_index"))) \
                .withColumn(col, (F.col(col) - F.col("tmp_lag_1")) / F.col("tmp_lag_1")).drop(F.col("tmp_lag_1"))\
                .withColumn(col, F.log(1+F.col(col)))
        return prices
    if not log_returns and is_spark:
        price_cols = prices.columns
        price_cols.remove("date_index")
        for col in price_cols:
            prices = prices\
                .withColumn("tmp_lag_1", F.lag(prices[col]) \
                                             .over(Window.orderBy("date_index"))) \
                .withColumn(col, (F.col(col) - F.col("tmp_lag_1")) / F.col("tmp_lag_1")).drop(F.col("tmp_lag_1"))
        return prices


def prices_from_returns(returns, log_returns=False):
    """
    Calculate the pseudo-prices given returns. These are not true prices because
    the initial prices are all set to 1, but it behaves as intended when passed
    to any PyPortfolioOpt method.

    :param returns: (daily) percentage returns of the assets
    :type returns: pd.DataFrame
    :param log_returns: whether to compute using log returns
    :type log_returns: bool, defaults to False
    :return: (daily) pseudo-prices.
    :rtype: pd.DataFrame
    """
    if log_returns:
        ret = np.exp(returns)
    else:
        ret = 1 + returns
    ret.iloc[0] = 1  # set first day pseudo-price
    return ret.cumprod()


def return_model(prices, method="mean_historical_return", **kwargs):
    """
    Compute an estimate of future returns, using the return model specified in ``method``.

    :param prices: adjusted closing prices of the asset, each row is a date
                   and each column is a ticker/id.
    :type prices: pd.DataFrame
    :param returns_data: if true, the first argument is returns instead of prices.
    :type returns_data: bool, defaults to False.
    :param method: the return model to use. Should be one of:

        - ``mean_historical_return``
        - ``ema_historical_return``
        - ``capm_return``

    :type method: str, optional
    :raises NotImplementedError: if the supplied method is not recognised
    :return: annualised sample covariance matrix
    :rtype: pd.DataFrame
    """
    if method == "mean_historical_return":
        return mean_historical_return(prices, **kwargs)
    elif method == "ema_historical_return":
        return ema_historical_return(prices, **kwargs)
    elif method == "capm_return":
        return capm_return(prices, **kwargs)
    else:
        raise NotImplementedError("Return model {} not implemented".format(method))


def mean_historical_return(prices, returns_data=False, compounding=True, frequency=252):
    """
    Calculate annualised mean (daily) historical return from input (daily) asset prices.
    Use ``compounding`` to toggle between the default geometric mean (CAGR) and the
    arithmetic mean.

    :param prices: adjusted closing prices of the asset, each row is a date
                   and each column is a ticker/id.
    :type prices: pd.DataFrame
    :param returns_data: if true, the first argument is returns instead of prices.
                         These **should not** be log returns.
    :type returns_data: bool, defaults to False.
    :param compounding: computes geometric mean returns if True,
                        arithmetic otherwise, optional.
    :type compounding: bool, defaults to True
    :param frequency: number of time periods in a year, defaults to 252 (the number
                      of trading days in a year)
    :type frequency: int, optional
    :return: annualised mean (daily) return for each asset
    :rtype: pd.Series
    """
    if not isinstance(prices, pd.DataFrame):
        warnings.warn("prices are not in a dataframe", RuntimeWarning)
        prices = pd.DataFrame(prices)
    if returns_data:
        returns = prices
    else:
        returns = returns_from_prices(prices)
    if compounding:
        return (1 + returns).prod() ** (frequency / returns.count()) - 1
    else:
        return returns.mean() * frequency


def ema_historical_return(
        prices, returns_data=False, compounding=True, span=500, frequency=252
):
    """
    Calculate the exponentially-weighted mean of (daily) historical returns, giving
    higher weight to more recent data.

    :param prices: adjusted closing prices of the asset, each row is a date
                   and each column is a ticker/id.
    :type prices: pd.DataFrame
    :param returns_data: if true, the first argument is returns instead of prices.
                         These **should not** be log returns.
    :type returns_data: bool, defaults to False.
    :param compounding: computes geometric mean returns if True,
                        arithmetic otherwise, optional.
    :type compounding: bool, defaults to True
    :param frequency: number of time periods in a year, defaults to 252 (the number
                      of trading days in a year)
    :type frequency: int, optional
    :param span: the time-span for the EMA, defaults to 500-day EMA.
    :type span: int, optional
    :return: annualised exponentially-weighted mean (daily) return of each asset
    :rtype: pd.Series
    """
    if not isinstance(prices, pd.DataFrame):
        warnings.warn("prices are not in a dataframe", RuntimeWarning)
        prices = pd.DataFrame(prices)
    if returns_data:
        returns = prices
    else:
        returns = returns_from_prices(prices)

    if compounding:
        return (1 + returns.ewm(span=span).mean().iloc[-1]) ** frequency - 1
    else:
        return returns.ewm(span=span).mean().iloc[-1] * frequency


def james_stein_shrinkage(prices, returns_data=False, compounding=True, frequency=252):
    raise NotImplementedError(
        "Deprecated because its implementation here was misguided."
    )


def capm_return(
        prices,
        market_prices=None,
        returns_data=False,
        risk_free_rate=0.02,
        compounding=True,
        frequency=252,
):
    """
    Compute a return estimate using the Capital Asset Pricing Model. Under the CAPM,
    asset returns are equal to market returns plus a :math:`\beta` term encoding
    the relative risk of the asset.

    .. math::

        R_i = R_f + \\beta_i (E(R_m) - R_f)


    :param prices: adjusted closing prices of the asset, each row is a date
                    and each column is a ticker/id.
    :type prices: pd.DataFrame
    :param market_prices: adjusted closing prices of the benchmark, defaults to None
    :type market_prices: pd.DataFrame, optional
    :param returns_data: if true, the first arguments are returns instead of prices.
    :type returns_data: bool, defaults to False.
    :param risk_free_rate: risk-free rate of borrowing/lending, defaults to 0.02.
                           You should use the appropriate time period, corresponding
                           to the frequency parameter.
    :type risk_free_rate: float, optional
    :param compounding: computes geometric mean returns if True,
                        arithmetic otherwise, optional.
    :type compounding: bool, defaults to True
    :param frequency: number of time periods in a year, defaults to 252 (the number
                        of trading days in a year)
    :type frequency: int, optional
    :return: annualised return estimate
    :rtype: pd.Series
    """
    if not isinstance(prices, pd.DataFrame):
        warnings.warn("prices are not in a dataframe", RuntimeWarning)
        prices = pd.DataFrame(prices)
    if returns_data:
        returns = prices
        market_returns = market_prices
    else:
        returns = returns_from_prices(prices)
        if market_prices is not None:
            market_returns = returns_from_prices(market_prices)
        else:
            market_returns = None
    # Use the equally-weighted dataset as a proxy for the market
    if market_returns is None:
        # Append market return to right and compute sample covariance matrix
        returns["mkt"] = returns.mean(axis=1)
    else:
        market_returns.columns = ["mkt"]
        returns = returns.join(market_returns, how="left")

    # Compute covariance matrix for the new dataframe (including markets)
    cov = returns.cov()
    # The far-right column of the cov matrix is covariances to market
    betas = cov["mkt"] / cov.loc["mkt", "mkt"]
    betas = betas.drop("mkt")
    # Find mean market return on a given time period
    if compounding:
        mkt_mean_ret = (1 + returns["mkt"]).prod() ** (
                frequency / returns["mkt"].count()
        ) - 1
    else:
        mkt_mean_ret = returns["mkt"].mean() * frequency

    # CAPM formula
    return risk_free_rate + betas * (mkt_mean_ret - risk_free_rate)


def _sec_in_days(num_days):
    """
    Calculate the seconds in a day

        :num_days: number of days to compute seconds for
        :type num_days: int
        :return: seconds in days
        :rtype: int
    """
    return_sec = num_days * 86400

    return return_sec
