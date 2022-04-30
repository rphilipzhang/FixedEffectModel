from ..utils.DemeanDataframe import demean_dataframe
from ..utils.FormTransfer import form_transfer
from ..utils.CalDf import cal_df
from ..utils.CalFullModel import cal_fullmodel
from ..utils.WaldTest import waldtest
from ..utils.OLSFixed import OLSFixed
from ..utils.ClusterErr import clustered_error, is_nested,min_clust
from ..utils.GenCrossProd import gencrossprod
from ..utils.RobustErr import robust_err


import statsmodels.api as sm
from scipy.stats import t
from scipy.stats import f

import numpy as np
import pandas as pd
import warnings

class fixedeffect:
    def __init__(self,
                 data_df,
                 dependent = None,
                 exog_x = None,
                 category=[],
                 cluster=[],
                 formula = None,
                 robust = False,
                 noint = False,
                 c_method='cgm',
                 psdef=True,
                 **kwargs
                 ):
        """
        :param data_df: Dataframe of relevant data
        :param y: List of dependent variables(so far, only support one dependent variable)
        :param exog_x: List of exogenous or right-hand-side variables (variable by time by entity).
        :param endog_x: List of endogenous variables
        :param iv: List of instrument variables
        :param category_input: List of category variables(fixed effects)
        :param cluster_input: List of cluster variables
        :param formula: a string like 'y~x+x2|id+firm|id',dependent_variable~continuous_variable|fixed_effect|clusters
        :param robust: bool value of whether to get a robust variance
        :param noint: force nointercept option
        :return:params,df,bse,tvalues,pvalues,rsquared,rsquared_adj,fvalue,f_pvalue,variance_matrix,fittedvalues,resid,summary
        **kwargs:some hidden option not supposed to be used by user
        """

        endog_x = []
        orignal_exog_x = exog_x
        iv = []
        no_print = False
        for key, value in kwargs.items():
            if key == 'no_print':
                if value == True:
                    no_print = True

        # grammar check
        if (exog_x is None) & (formula is None):
            raise NameError('You have to input list of variables name or formula')
        elif exog_x is None:
            dependent, exog_x, category_input, cluster_input, endog_x, iv = form_transfer(formula)
            if no_print==False:
                print('dependent variable(s):', dependent)
                print('independent(exogenous):', exog_x)
                print('category variables(fixed effects):', category_input)
                print('cluster variables:', cluster_input)
        else:
            dependent, exog_x, category_input, cluster_input, endog_x, iv = dependent, exog_x, category,  \
                                                                              cluster, endog_x, iv

        # df preprocess
        data_df.fillna(0, inplace=True)
        data_df.reset_index(drop=True, inplace=True)
        data_df = gencrossprod(data_df, exog_x)

        self.data_df = data_df
        self.dependent = dependent
        self.exog_x = exog_x
        self.endog_x = endog_x
        self.iv = iv
        self.category_input = category_input
        self.cluster_input = cluster_input
        self.formula = formula
        self.robust = robust
        self.noint = noint
        self.c_method = c_method
        self.psdef = psdef
        self.no_print = no_print
        self.orignal_exog_x = orignal_exog_x

    def fit(self,
            epsilon=1e-8,
            max_iter=1e6):

        data_df = self.data_df
        dependent = self.dependent
        exog_x = self.exog_x
        endog_x = self.endog_x
        iv = self.iv
        category_input = self.category_input
        noint = self.noint
        orignal_exog_x = self.orignal_exog_x

        if noint is True:
            k0 = 0
        else:
            k0 = 1

        # if on level data:
        if (category_input == []):
            demeaned_df = data_df.copy()
            if noint is False:
                demeaned_df['const'] = 1
            rank = 0
        # if on demean data:
        else:
            all_cols = []
            for i in exog_x:
                all_cols.append(i)
            for i in endog_x:
                all_cols.append(i)
            for i in iv:
                all_cols.append(i)
            all_cols.append(dependent[0])
            demeaned_df = demean_dataframe(data_df, all_cols, category_input, epsilon = epsilon, max_iter = max_iter)

            if noint is False:
                for i in all_cols:
                    demeaned_df[i] = demeaned_df[i].add(data_df[i].mean())

                demeaned_df['const'] = 1
            rank = cal_df(data_df, category_input)

        #----------------- estimation  -----------------#
        # if OLS on raw data:
        if noint is False:
            exog_x = ['const'] + exog_x

        model = sm.OLS(demeaned_df[dependent].astype(float), demeaned_df[exog_x].astype(float))
        result = model.fit()
        coeff = result.params.values.reshape(len(exog_x), 1)

        real_resid = demeaned_df[dependent] - np.dot(demeaned_df[exog_x], coeff)
        demeaned_df['resid'] = real_resid

        n = demeaned_df.shape[0]
        k = len(exog_x)

        # initiate result object
        f_result = OLSFixed()
        f_result.model = 'fixedeffect'
        f_result.dependent = dependent
        f_result.exog_x = exog_x
        f_result.endog_x = []
        f_result.iv = []
        f_result.category_input = category_input
        f_result.data_df = data_df.copy()
        f_result.demeaned_df = demeaned_df
        f_result.params = result.params
        f_result.df = result.df_resid - rank + k0
        f_result.x_second_stage = None
        f_result.x_first_stage = None
        f_result.treatment_input = None
        f_result.orignal_exog_x = orignal_exog_x
        f_result.cluster = []


        # compute standard error and save in result
        self.compute_se(result, f_result, n, k, rank)

        # compute summary statistics and save in result
        self.compute_summary_statistics(result, f_result, rank)


        # debug
        df_result = pd.DataFrame(columns=['bse'], index=list(result.params.index))
        df_result['bse'] = f_result.bse
        f_result.bse = df_result['bse']

        return f_result


    def compute_summary_statistics(self,
                                   result,
                                   f_result,
                                   rank):

        dependent = self.dependent
        category_input = self.category_input
        cluster_input = self.cluster_input
        data_df = self.data_df
        robust = self.robust
        c_method = self.c_method

        exog_x = f_result.exog_x

        if self.noint is True:
            k0 = 0
        else:
            k0 = 1

        demeaned_df = f_result.demeaned_df
        n = demeaned_df.shape[0]
        k = len(exog_x)

        f_result.resid = demeaned_df['resid']
        f_result.tvalues = f_result.params / f_result.bse
        f_result.pvalues = pd.Series(2 * t.sf(np.abs(f_result.tvalues), f_result.df), index=list(result.params.index))
        proj_rss = sum(f_result.resid ** 2)
        proj_rss = float("{:.8f}".format(proj_rss))  # round up

        # calculate totoal sum squared of error
        if k0 == 0 and category_input == []:
            proj_tss = sum(((demeaned_df[dependent]) ** 2).values)[0]
        else:
            proj_tss = sum(((demeaned_df[dependent] - demeaned_df[dependent].mean()) ** 2).values)[0]

        proj_tss = float("{:.8f}".format(proj_tss))  # round up
        if proj_tss > 0:
            f_result.rsquared = 1 - proj_rss / proj_tss
        else:
            raise NameError('Total sum of square equal 0, program quit.')

        # calculate adjusted r2
        if category_input != []:
            # for fixed effect, k0 should not affect adjusted r2
            f_result.rsquared_adj = 1 - (len(data_df) - 1) / (result.df_resid - rank + k0) * (1 - f_result.rsquared)
        else:
            f_result.rsquared_adj = 1 - (len(data_df) - k0) / (result.df_resid) * (1 - f_result.rsquared)

        if k0 == 0:
            w = waldtest(f_result.params, f_result.variance_matrix)
        else:
            # get rid of constant in the vc matrix
            f_var_mat_noint = f_result.variance_matrix.copy()
            if type(f_var_mat_noint) == np.ndarray:
                f_var_mat_noint = np.delete(f_var_mat_noint, 0, 0)
                f_var_mat_noint = np.delete(f_var_mat_noint, 0, 1)
            else:
                f_var_mat_noint = f_var_mat_noint.drop('const', axis=1)
                f_var_mat_noint = f_var_mat_noint.drop('const', axis=0)

            # get rid of constant in the param column
            params_noint = f_result.params.drop('const', axis=0)
            if category_input == []:
                w = waldtest(params_noint, (n - k) / (n - k - rank) * f_var_mat_noint)
            else:
                w = waldtest(params_noint, f_var_mat_noint)

        # calculate f-statistics
        if result.df_model > 0:
            # if do pooled regression
            if category_input == []:
                # if do pooled regression, because doesn't account for const in f test, adjust dof
                scale_const = (n - k) / (n - k + k0)
                f_result.fvalue = scale_const * w / result.df_model
            # if do fixed effect, just ignore
            else:
                f_result.fvalue = w / result.df_model
        else:
            f_result.fvalue = 0

        if len(cluster_input) > 0 and cluster_input[0] != '0' and c_method == 'cgm':
            f_result.f_pvalue = f.sf(f_result.fvalue, result.df_model,
                                     min(min_clust(data_df, cluster_input) - 1, f_result.df))
            f_result.f_df_proj = [result.df_model, (min(min_clust(data_df, cluster_input) - 1, f_result.df))]
        else:
            f_result.f_pvalue = f.sf(f_result.fvalue, result.df_model, f_result.df)
            f_result.f_df_proj = [result.df_model, f_result.df]

        f_result.fittedvalues = result.fittedvalues

        # get full-model related statistics
        f_result.full_rsquared, f_result.full_rsquared_adj, f_result.full_fvalue, f_result.full_f_pvalue, f_result.f_df_full \
            = cal_fullmodel(data_df, dependent, exog_x, cluster_input, rank, RSS=sum(f_result.resid ** 2),
                            originRSS=sum(result.resid ** 2))

        f_result.nobs = result.nobs
        f_result.yname = dependent
        f_result.xname = exog_x
        f_result.resid_std_err = np.sqrt(sum(f_result.resid ** 2) / (result.df_resid - rank))
        if len(cluster_input) == 0 or cluster_input[0] == '0':
            f_result.cluster_method = 'no_cluster'
            if robust:
                f_result.Covariance_Type = 'robust'
            else:
                f_result.Covariance_Type = 'nonrobust'
        else:
            f_result.cluster_method = c_method
            f_result.Covariance_Type = 'clustered'

        return

    # compute standard error
    def compute_se(self, result, f_result, n, k, rank):

        if self.noint is True:
            k0 = 0
        else:
            k0 = 1

        cluster_col = self.cluster_input
        category_col = self.category_input

        robust = self.robust
        c_method = self.c_method
        psdef = self.psdef

        exog_x = f_result.exog_x
        demeaned_df = f_result.demeaned_df


        if (len(cluster_col) == 0 or cluster_col[0] == '0') & (robust is False):
            if (len(category_col) == 0):
                std_error = result.bse * np.sqrt((n - k) / (n - k - rank))  # for pooled regression
            else:
                std_error = result.bse * np.sqrt((n - k) / (n - k + k0 - rank))  # for fe if k0=1 need to add it back
            covariance_matrix = result.normalized_cov_params * result.scale * result.df_resid / f_result.df
        elif (len(cluster_col) == 0 or cluster_col[0] == '0') & (robust is True):
            covariance_matrix = robust_err(demeaned_df, exog_x, category_col, n, k, k0, rank)
            std_error = np.sqrt(np.diag(covariance_matrix))
        else:
            if category_col == []:
                nested = False
            else:
                nested = is_nested(f_result.demeaned_df, category_col, cluster_col, exog_x)
                if self.no_print==False:
                    print('category variable(s) is_nested in cluster variables:', nested)

            covariance_matrix = clustered_error(demeaned_df,
                                                exog_x,
                                                category_col,
                                                cluster_col,
                                                n, k, k0, rank,
                                                nested = nested,
                                                c_method=c_method,
                                                psdef=psdef)

            std_error = np.sqrt(np.diag(covariance_matrix))

        f_result.bse = std_error
        f_result.variance_matrix = covariance_matrix

        return






