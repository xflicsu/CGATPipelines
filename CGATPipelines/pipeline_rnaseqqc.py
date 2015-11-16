##########################################################################
#
#   MRC FGU Computational Genomics Group
#
#   $Id$
#
#   Copyright (C) 2009 Tildon Grant Belgard
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
##########################################################################

"""
====================
ReadQc pipeline
====================

:Author: Tom Smith
:Release: $Id$
:Date: |today|
:Tags: Python

The rnaseqqc pipeline rapidly estimates gene/transcript abundance so
that biases in abundances can be visually inspected prior to any
analyses.

With regards to differential expression analyses, the purposes of this
are twofold:
    1. Identify consitent biases which will affect power to detect DE
    2. Identify differential biases between samples which may lead to
    erroneous DE calls downstream

Sailfish is utlised to rapidly estimate transcript abundance. This
requires a multi-fasta transcripts file. Although Sailfish can
estimate abundance over transcript models, it may be more sensible to
use collapsed gene models to increase accuracy of adundance estimation

For further details see http://www.cs.cmu.edu/~ckingsf/software/sailfish/

Altenatively, the user can specify the location of a quantification
table containing expression estimates for all the genes/transcripts in
the multi-fasta file. The order of the genes/transcripts must be
exactly the same as the multi-fasta file and all genes/transcripts
must be present and labelled exactly as in the multi-fasta file. The
header should contain a "Name" column and a single column for
each sample, labelled with the same.

Below is an example of a multi-fasta file and the corresponding
abundance file as generated by sailfish or the user:

zcat transcripts.ffn.gz

>dummygene1
acgatctacgatcgatcgcgcgatcgcgagatacgacgatcacgactactacaggagcgatca
>dummygene2
atacttatattcatattactacgcgattatcatctatcggcgcgattctacgacgata

zcat abundance_estimates.tsv.gz

Name      delta-N-1     delta-N-2
dummygene1      1758.59       1543.52
dummygene2      906.643       852.103



Individual tasks are enabled in the configuration file.

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning`
on general information how to use CGAT pipelines.

Configuration
-------------

No general configuration required.

Input
-----

If quantification is required, reads are imported by placing files or
linking to files in the :term: `working directory`.

The default file format assumes the following convention:

   <sample>-<condition>-<replicate>.<suffix>

``sample`` and ``condition`` make up an :term:`experiment`,
while ``replicate`` denotes the :term:`replicate` within an :term:`experiment`.
The ``suffix`` determines the file type.
The following suffixes/file types are possible:

sra
   Short-Read Archive format. Reads will be extracted using the :file:
   `fastq-dump` tool.

fastq.gz
   Single-end reads in fastq format.

fastq.1.gz, fastq2.2.gz
   Paired-end reads in fastq format.
   The two fastq files must be sorted by read-pair.

.. note::

   Quality scores need to be of the same scale for all input files.
   Thus it might be difficult to mix different formats.

Pipeline output
===============

The major output is a set of HTML pages and plots reporting on the
apparent biases in transcript abudance within the sequence archive

Example
=======

Example data is available at
http://www.cgat.org/~andreas/sample_data/pipeline_rnaseqqc.tgz.
To run the example, simply unpack and untar::

   wget http://www.cgat.org/~andreas/sample_data/pipeline_readqc.tgz
   tar -xvzf pipeline_readqc.tgz
   cd pipeline_readqc
   python <srcdir>/pipeline_readqc.py make full

Requirements:

sailfish

Code
====

ToDo
====
Documentation

"""

###################################################
###################################################
###################################################
# load modules
###################################################

# import ruffus
from ruffus import *

# import useful standard python modules
import sys
import os
import re
import glob
import cStringIO
import numpy
import pandas
from scipy.stats import linregress
import itertools as iter

import CGAT.Experiment as E
import CGAT.IOTools as IOTools
import CGATPipelines.PipelineMapping as PipelineMapping
import CGATPipelines.Pipeline as P
import CGAT.CSV2DB as CSV2DB

###################################################
###################################################
###################################################
# Pipeline configuration
###################################################

# load options from the config file
P.getParameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"])
PARAMS = P.PARAMS

#########################################################################
#########################################################################
#########################################################################
# define input files
INPUT_FORMATS = ("*.fastq.1.gz", "*.fastq.gz", "*.sra", "*.csfasta.gz")
REGEX_FORMATS = regex(r"(\S+).(fastq.1.gz|fastq.gz|sra|csfasta.gz)")


####################################################
# bias analysis
####################################################


@transform(PARAMS["sailfish_transcripts"],
           regex("(\S+)"),
           "index/transcriptome.sfi")
def indexForSailfish(infile, outfile):
    '''create a sailfish index'''

    statement = '''
    sailfish index --transcripts=%(infile)s -k %(sailfish_kmer_size)i
    --out=%(outfile)s '''

    P.run()


@follows(indexForSailfish, mkdir("quantification"))
@transform(INPUT_FORMATS,
           REGEX_FORMATS,
           add_inputs(indexForSailfish),
           r"quantification/\1/\1_quant.sf")
def runSailfish(infiles, outfile):
    '''quantify abundance'''

    job_threads = PARAMS["sailfish_threads"]

    infile, index = infiles
    index = P.snip(index, "/transcriptome.sfi")

    sailfish_bootstrap = 0

    m = PipelineMapping.Sailfish(strand=PARAMS["sailfish_strandedness"],
                                 orient=PARAMS["sailfish_orientation"],
                                 threads=PARAMS["sailfish_threads"])

    statement = m.build((infile,), outfile)

    P.run()


if PARAMS["sailfish"]:
    @follows(runSailfish)
    @merge(runSailfish,
           "abundance_estimates.tsv")
    def mergeResults(infiles, outfile):
        ''' merge the TPM values per sample'''

        sample_id = os.path.basename(os.path.dirname(infiles[0]))
        df = pandas.read_table(
            infiles[0], sep='\t', comment='#', header=0, index_col=0)
        df.columns = ["Length", sample_id, "NumReads"]
        df.index.name = "Name"
        df.drop(["Length", "NumReads"], axis=1, inplace=True)

        for infile in infiles[1:]:
            sample_id = os.path.basename(os.path.dirname(infile))

            tmp_df = pandas.read_table(
                infile, sep='\t', comment='#', header=0, index_col=0)
            tmp_df.columns = ["Length", sample_id, "NumReads"]
            tmp_df.index.name = "Name"
            tmp_df.drop(["Length", "NumReads"], axis=1, inplace=True)

            df = pandas.merge(df, tmp_df, left_index=True, right_index=True)

        df.to_csv(outfile, sep="\t")
else:
    @follows(mkdir("quant.dir"))
    @originate("abundance_estimates.tsv")

    def mergeResults(outfile):
        infile = PARAMS["abundance_file"]
        base = os.path.basename(infile)
        statement = '''ln -s %(infile)s; mv %(base)s %(outfile)s'''
        P.run()


@transform(PARAMS["sailfish_transcripts"],
           regex("(\S+)"),
           "transcripts_attributes.tsv.gz")
# take multifasta transcripts file and output file of attributes
def characteriseTranscripts(infile, outfile):

    if infile.endswith(".gz"):
        statement = '''zcat %(infile)s'''
    else:
        statement = '''cat %(infile)s'''
    statement += '''
    | python %(scriptsdir)s/fasta2table.py
    --split-fasta-identifier --section=na,dn,length -v 0
    | gzip > %(outfile)s'''

    P.run()


# where should this code be moved to?.. module file?
@follows(characteriseTranscripts)
@transform(characteriseTranscripts,
           regex("transcripts_attributes.tsv.gz"),
           add_inputs(mergeResults),
           ["quant.dir/binned_means_correlation.tsv",
            "quant.dir/binned_means_gradients.tsv"])
def summariseBias(infiles, outfiles):

    transcripts, expression = infiles
    out_correlation, out_gradient = outfiles

    atr = pandas.read_csv(transcripts, sep='\t',
                          compression="gzip", index_col="id")
    exp = pandas.read_csv(expression, sep='\t')
    atr = atr.rename(columns={'pGC': 'GC_Content'})

    def percentage(x):
        return float(x[0])/float(x[1])

    for di in iter.product("ATCG", repeat=2):
        di = di[0]+di[1]
        temp_df = atr.loc[:, [di, "length"]]
        atr[di] = temp_df.apply(percentage, axis=1)

    drop_cols = (["nAT", "nGC", "pAT", "pA", "pG", "pC", "pT", "nA",
                  "nG", "nC", "nT", "ncodons",
                  "mCountsOthers", "nUnk", "nN", "pN"])
    atr = atr.drop(drop_cols, axis=1)
    atr["length"] = numpy.log2(atr["length"])

    log_exp = numpy.log2(exp.ix[:, 1:]+0.1)

    log_exp["id"] = exp[["Name"]]
    log_exp = log_exp.set_index("id")

    bias_factors = list(atr.columns)
    samples = list(exp.columns)
    samples.remove("Name")

    merged = atr.merge(log_exp, left_index="id", right_index="id")

    def lin_reg_grad(x, y):
        slope, intercept, r, p, stderr = linregress(x, y)
        return slope

    def aggregate_by_factor(df, attribute, sample_names, bins, function):

        temp_dict = dict.fromkeys(sample_names, function)
        temp_dict[attribute] = function
        means_df = df.groupby(pandas.qcut(df.ix[:, attribute], bins))
        means_df = means_df.agg(temp_dict).sort(axis=1)
        atr_values = means_df[attribute]
        means_df.drop(attribute, axis=1, inplace=True)
        means_df = (means_df-means_df.min()) / (means_df.max()-means_df.min())
        means_df[attribute] = atr_values
        corr_matrix = means_df.corr(method='spearman')
        corr_matrix = corr_matrix[corr_matrix.index != attribute]
        factor_gradients = []
        for sample in samples:
            factor_gradients.append(lin_reg_grad(y=means_df[sample],
                                                 x=means_df[factor]))
        return means_df, corr_matrix, factor_gradients

    corr_matrices = {}
    gradient_lists = {}
    for factor in bias_factors:
        means_binned, corr_matrix, gradients = aggregate_by_factor(
            merged, factor, samples, PARAMS["bias_bin"], numpy.mean)
        outfile_means = "quant.dir/means_binned_%s.tsv" % factor
        means_binned.to_csv(outfile_means, sep="\t",
                            index=False, float_format='%.4f')
        corr_matrices[factor] = list(corr_matrix[factor])
        gradient_lists[factor] = gradients

    corr_matrix_df = pandas.DataFrame.from_dict(
        corr_matrices, orient='columns', dtype=None)
    corr_matrix_df["sample"] = sorted(samples)

    gradient_df = pandas.DataFrame.from_dict(
        gradient_lists, orient='columns', dtype=None)
    gradient_df["sample"] = sorted(samples)

    corr_matrix_df.to_csv(out_correlation, sep="\t",
                          index=False, float_format='%.6f')

    gradient_df.to_csv(out_gradient, sep="\t",
                       index=False, float_format='%.6f')


@follows(summariseBias)
@transform(summariseBias,
           suffix(".tsv"),
           ".load")
def loadBiasSummary(infiles, outfiles):
    for inf in glob.glob("quant.dir/*.tsv"):
        P.load(inf, inf.replace(".tsv", ".load"))

#########################################################################


@follows(loadBiasSummary)
def full():
    pass


@follows(runSailfish)
def sail():
    pass


#########################################################################


@follows()
def publish():
    '''publish files.'''
    P.publish_report()


@follows(mkdir("report"))
def build_report():
    '''build report from scratch.'''

    E.info("starting documentation build process from scratch")
    P.run_report(clean=True)


@follows(mkdir("report"))
def update_report():
    '''update report.'''

    E.info("updating documentation")
    P.run_report(clean=False)

if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
