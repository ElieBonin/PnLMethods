from pnl_report.methods import PnLMethods, DataFormat

from functools import reduce
import pandas as pd
import numpy as np


class PnLReport:

    def __init__(self, data, id_col='ticker', method='fifo', **kwargs):

        self.raw_data = data
        self.pnls = pd.DataFrame()

        self.reports = {k: None for k in data[id_col].dropna().unique().tolist()}
        self.inputs = {**{'id_col': id_col, 'method': method}, **kwargs}
        self.cols = {**DataFormat.COLS, **kwargs}

    def _report_pnl(self, k):
        df = pd.DataFrame.from_records(self.reports[k].pnls).assign(**{self.inputs.get('id_col', 'id_col'): k})
        return df

    # Properties

    @property
    def result_pnl(self):
        """Returns a DF, with unwind dates as index, with P&L time series for each ticker"""

        if self.pnls.shape[0] == 0:
            return pd.DataFrame(columns=list(self.reports.keys()) + ['pnl_total'])

        st_dt = min(self.pnls['unwind_date'])
        ls = list(self.reports.keys())

        if self.inputs.get('extend_date', False) and not isinstance(st_dt, (int, float)):
            ed_dt = max(self.pnls['unwind_date'])
            idx = [d.strftime('%Y-%m-%d') for d in pd.date_range(st_dt, ed_dt)]

        else:
            idx = self.pnls['unwind_date'].unique().tolist()

        dfs = [pd.DataFrame(index=idx, columns=ls)]

        for k in ls:
            sub_df = self._report_pnl(k).assign(pnl_cumsum=None)

            if sub_df.shape[0]:
                sub_df = sub_df.loc[sub_df['unwind_date'] >= st_dt]
                sub_df['pnl_cumsum'] = sub_df[self.cols['pnl_col']].cumsum()

                sub_df = sub_df.drop_duplicates(subset=['unwind_date'], keep='last')
                sub_df = sub_df[['unwind_date', 'pnl_cumsum']].groupby('unwind_date').sum()
            else:
                sub_df = pd.DataFrame(columns=['unwind_date', 'pnl_cumsum']).set_index('unwind_date')

            sub_df = sub_df.rename(columns={'pnl_cumsum': k})
            dfs.append(sub_df)

        df = reduce(lambda l, r: l.drop(columns=[r.columns[0]], errors='ignore')
                    .merge(r, left_index=True, right_index=True, how='left'), dfs).ffill().replace(np.NaN, 0)

        df['pnl_total'] = df.sum(axis=1)

        return df

    # Run Function

    def get_pnl_schedule(self, st_dt: str = None, ed_dt: str = None, ffill: bool = False) -> pd.DataFrame:
        if self.pnls.shape[0] == 0:
            return pd.DataFrame(columns=list(self.reports.keys()) + ['pnl_total'])

        _st_dt = min(self.pnls['unwind_date'])
        ls = list(self.reports.keys())

        if self.inputs.get('extend_date', False) and not isinstance(_st_dt, (int, float)):
            ed_dt = max(self.pnls['unwind_date'])
            idx = [d.strftime('%Y-%m-%d') for d in pd.date_range(_st_dt, ed_dt)]

        else:
            idx = self.pnls['unwind_date'].unique().tolist()

        dfs = [pd.DataFrame(index=idx, columns=ls)]

        for k in ls:
            sub_df = self._report_pnl(k).assign(pnl_cumsum=None)

            if sub_df.shape[0]:
                sub_df = sub_df.loc[sub_df['unwind_date'] >= _st_dt]
                sub_df = sub_df[['unwind_date', self.cols['pnl_col']]].groupby('unwind_date').sum()

            #     ---

                # sub_df = sub_df.loc[sub_df['unwind_date'] >= st_dt]
                # sub_df['pnl_cumsum'] = sub_df[self.cols['pnl_col']].cumsum()
                #
                # sub_df = sub_df.drop_duplicates(subset=['unwind_date'], keep='last')
                # sub_df = sub_df[['unwind_date', 'pnl_cumsum']].groupby('unwind_date').sum()


            else:
                sub_df = pd.DataFrame(columns=['unwind_date', self.cols['pnl_col']]).set_index('unwind_date')

            sub_df = sub_df.rename(columns={self.cols['pnl_col']: k})
            dfs.append(sub_df)

        df = reduce(lambda l, r: l.drop(columns=[r.columns[0]], errors='ignore')
                    .merge(r, left_index=True, right_index=True, how='left'), dfs)

        if ffill:
            df = df.ffill().replace(np.NaN, 0)
            df['pnl_total'] = df.sum(axis=1)

        df = df.loc[df.index >= st_dt] if st_dt else df
        df = df.loc[df.index <= ed_dt] if ed_dt else df

        return df

    def run(self):

        # Data

        self.raw_data = DataFormat.fmt(self.raw_data, self.cols)
        self.reports = {k: PnLMethods(data=self.raw_data.loc[self.raw_data[self.inputs['id_col']] == k]
                                      .drop(columns=[self.inputs['id_col']]), **self.inputs).run()
                        for k in self.reports.keys()}

        # PnL

        self.pnls = pd.concat([self._report_pnl(k) for k in self.reports.keys()], ignore_index=True)\
            .sort_values(by=['unwind_date', 'date'])

        return self

    def clean(self):
        """Cleaning the dataset keeps open trades, by keeping stacked content."""
        raw_data_ls = []

        for k in self.reports.keys():

            df = pd.DataFrame.from_records(self.reports[k].stack).assign(**{self.inputs.get('id_col', 'id_col'): k})
            self.reports[k] = PnLMethods(data=df, **self.inputs).run()
            raw_data_ls.append(df)

        self.raw_data = pd.concat(raw_data_ls, sort=False, ignore_index=True)

        return self


class PnLProjection(PnLReport):
    """To be used to assess the P&L resulting in potential trades"""

    def __init__(self, data, trades, **kwargs):
        super().__init__(data=data, **kwargs)
        self._trades = trades
        super().run().clean()
        self.verbose = kwargs.get('verbose', True)
        self.run()

    @property
    def trades(self):
        """If no date column is provided, it will take the max existing one from raw_data, plus cumulative days"""
        return DataFormat.fmt(df=self._trades.copy(), cols=self.cols)

    def run(self):
        """Computes P&L based on potential trades. It cleans the data set first, aka it keeps only open trades"""

        _1 = {k: sum(l[self.cols['qty_col']] for l in self.reports[k].stack) for k in self.reports.keys()}
        _2 = {k: self.trades.loc[self.trades[self.inputs['id_col']] == k][self.cols['qty_col']].sum() for k
              in set(self.trades[self.inputs['id_col']].tolist())}

        _1 = {k: v / abs(v) for k, v in _1.items() if v != 0}
        _2 = {k: v / abs(v) for k, v in _2.items() if v != 0}

        ls = [k for k in _1.keys() if _1[k] == _2[k]]

        # ---

        _ = [self.reports.pop(k) for k in ls]

        self.raw_data = pd.concat([self.raw_data, self.trades], sort=False, ignore_index=True)
        super().run()
        self.print()

        return self

    def print(self):
        """Prints the P&L results per ticker"""

        if self.result_pnl.shape[0] and self.verbose:
            _ = self.result_pnl.tail(1).reset_index(drop=True).to_dict(orient='index')[0]
            _ = [print(f"Projected P&L for {k}: {v}") for k, v in _.items()]


if __name__ == '__main__':
    df = pd.DataFrame()

    df['qty'] = [12, 20, 9, -5, -1, -2, -10]
    df['price'] = [10, 12, 14, 25, 12, 12, 22.5]
    df['ticker'] = ['RDSA LN', 'GOOG US', 'RDSA LN', 'GOOG US', 'RDSA LN', 'GOOG US', 'GOOG US']

    df['side'] = 'BUY'
    df.loc[df['qty'] < 0, 'side'] = 'SELL'

    df['date'] = ['2020-01-01', '2020-01-02', '2020-01-03', '2020-01-04', '2020-04-04', '2020-04-04', '2020-06-10']

    rep = PnLReport(df, extend_date=False).run()  # .clean()
    res = rep.result_pnl

    # P&L Projection

    dq = pd.DataFrame()

    # dq['date'] = ['2020-06-15', '2020-06-15', '2020-06-17']
    dq['qty'] = [-3, -100, 5]
    dq['price'] = [10, 13, 10]
    dq['ticker'] = ['GOOG US', 'RDSA LN', 'RDSA LN']

    proj = PnLProjection(df, trades=dq)

    print()
