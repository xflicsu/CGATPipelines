##############################################################################
#
#   MRC FGU CGAT
#
#   $Id$
#
#   Copyright (C) 2009 Andreas Heger
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
###############################################################################
"""===========================
Pipeline Splicing
===========================

:Author: Jakub Scaber
:Release: $Id$
:Date: |today|
:Tags: Python

Overview
========

rMATS a computational tool to detect differential alternative splicing events
from RNA-Seq data. The statistical model of MATS calculates the P-value and
false discovery rate that the difference in the isoform ratio of a gene
between two conditions exceeds a given user-defined threshold. From the
RNA-Seq data, MATS can automatically detect and analyze alternative splicing
events corresponding to all major types of alternative splicing patterns.
MATS handles replicate RNA-Seq data from both paired and unpaired study design.

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general
information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.ini` file.
CGATReport report requires a :file:`conf.py` and optionally a
:file:`cgatreport.ini` file (see :ref:`PipelineReporting`).

Default configuration files can be generated by executing:

   python <srcdir>/pipeline_splicing.py config

Input files
-----------

".bam" files generated using STAR or Tophat2. Other mappers
may also work.

Design_files ("*.design.tsv") are used to specify sample variates. The
minimal design file is shown below, where include specifies if the
sample should be included in the analysis, group specifies the sample
group and pair specifies whether the sample is paired. Note, multiple
design files may be included, for example so that multiple models can
be fitted to different subsets of the data

(tab-seperated values)

sample    include    group    pair
WT-1-1    1    WT    0
WT-1-2    1    WT    0
Mutant-1-1    1    Mutant    0
Mutant-1-2    1    Mutant    0

The pipeline can only handle comparisons between two conditions with
replicates. If further comparisons are needed, further design files
should be used.

Requirements
------------

The pipeline requires the results from
:doc:`pipeline_annotations`. Set the configuration variable
:py:data:`annotations_database` and :py:data:`annotations_dir`.

Requirements:

* samtools
* DEXSeq
* rMATS
* pysam

Pipeline output
===============

For each experiment, the output from rMATS is placed in the results.dir
folder. Each experiment is found in a subdirectory named designfilename.dir

rMATS output is further described here:
http://rnaseq-mats.sourceforge.net/user_guide.htm



Glossary
========

.. glossary::


Code
====

"""
from ruffus import *
import sys
import os
import glob
import sqlite3
from rpy2.robjects import r as R
import CGAT.BamTools as BamTools
import CGAT.Experiment as E
import CGAT.Expression as Expression
import CGAT.GTF as GTF
import CGAT.IOTools as IOTools
import CGATPipelines.Pipeline as P
import CGATPipelines.PipelineTracks as PipelineTracks
import CGATPipelines.PipelineSplicing as PipelineSplicing

# load options from the config file
PARAMS = P.getParameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"])

# add configuration values from associated pipelines
#
# 1. pipeline_annotations: any parameters will be added with the
#    prefix "annotations_". The interface will be updated with
#    "annotations_dir" to point to the absolute path names.
PARAMS = P.PARAMS
PARAMS.update(P.peekParameters(
    PARAMS["annotations_dir"],
    "pipeline_annotations.py",
    prefix="annotations_",
    update_interface=True,
    restrict_interface=True))  # add config values from associated pipelines


pythonScriptsDir = R('''
    f = function(){
    pythonScriptsDir = system.file("python_scripts", package="DEXSeq")
    }
    f()''').tostring()


# -----------------------------------------------
# Utility functions
def connect():
    '''Connect to database (sqlite by default)

    This method also attaches to helper databases.
    '''

    dbh = sqlite3.connect(PARAMS["database_name"])
    statement = '''ATTACH DATABASE '%s' as annotations''' % (
        PARAMS["annotations_database"])
    cc = dbh.cursor()
    cc.execute(statement)
    cc.close()

    return dbh


class MySample(PipelineTracks.Sample):
    attributes = tuple(PARAMS["attributes"].split(","))

TRACKS = PipelineTracks.Tracks(MySample).loadFromDirectory(
    glob.glob("*.bam"), "(\S+).bam")


Sample = PipelineTracks.AutoSample
DESIGNS = PipelineTracks.Tracks(Sample).loadFromDirectory(
    glob.glob("*.design.tsv"), "(\S+).design.tsv")

# ---------------------------------------------------
# Specific pipeline tasks


@mkdir("results.dir")
@files(PARAMS["annotations_interface_geneset_all_gtf"],
       "geneset.gtf")
def buildGtf(infile, outfile):
    '''creates a gtf

    This takes ensembl annotations (geneset_all.gtf.gz) and writes out
    all entries that have a 'source' match to "rRNA" or 'contig' match
    to "chrM" for use as a mask.

    Parameters
    ----------

    infile : string
        :term:`gtf` file of ensembl annotations e.g. geneset_all.gtf.gz

    annotations_interface_table_gene_info : string
        :term:`PARAMS` gene_info table in annotations database - set
        in pipeline.ini in annotations directory

    annotations_interface_table_gene_stats : string
        :term:`PARAMS` gene_stats table in annotations database
        - set in pipeline.ini in annotations directory

    outfile : string

        A :term:`gtf` file for use as "mask file" for cufflinks.  This
        is created by filtering infile for certain transcripts e.g.
        rRNA or chrM transcripts and writing them to outfile

    '''

    dbh = connect()

    try:
        select = dbh.execute("""SELECT DISTINCT gene_id FROM %s
        WHERE gene_biotype = 'rRNA';""" % os.path.basename(
            PARAMS["annotations_interface_table_gene_info"]))
        rrna_list = [x[0] for x in select]
    except sqlite3.OperationalError as e:
        E.warn("can't select rRNA annotations, error='%s'" % str(e))
        rrna_list = []

    try:
        select2 = dbh.execute("""SELECT DISTINCT gene_id FROM %s
        WHERE contig = 'chrM';""" % os.path.basename(
            PARAMS["annotations_interface_table_gene_stats"]))
        chrM_list = [x[0] for x in select2]
    except sqlite3.OperationalError as e:
        E.warn("can't select rRNA annotations, error='%s'" % str(e))
        chrM_list = []

    geneset = IOTools.openFile(infile)
    outf = IOTools.openFile(outfile, "wb")
    for entry in GTF.iterator(geneset):
        if entry.gene_id not in rrna_list or entry.gene_id not in chrM_list:
            outf.write("\t".join((map(
                str,
                [entry.contig, entry.source, entry.feature,
                 entry.start, entry.end, ".", entry.strand,
                 ".", "transcript_id" + " " + '"' +
                 entry.transcript_id + '"' + ";" + " " +
                 "gene_id" + " " + '"' + entry.gene_id + '"']))) + "\n")

    outf.close()


@transform(buildGtf,
           suffix(".gtf"),
           ".gff")
def buildGff(infile, outfile):
    '''Creates a gff for DEXSeq

    This takes the gtf and flattens it to an exon based input
    required by DEXSeq

    Parameters
    ----------

    infile : string
        :term:`gtf` output from buildGtf function

    outfile : string
        A :term:`gff` file for use in DEXSeq'''

    ps = pythonScriptsDir

    statement = '''python %(ps)s/dexseq_prepare_annotation.py %(infile)s %(outfile)s'''
    P.run()


@transform(glob.glob("*.bam"),
           regex("(\S+).bam"),
           add_inputs(buildGff),
           r"counts.dir/\1.txt")
def countDEXSeq(infiles, outfile):
    '''creates counts for DEXSeq

    This takes the gtf and flattens it to an exon based input
    required by DEXSeq

    Parameters
    ----------

    infile[0]: string
        :term:`bam` file input

    infile[1]: string
        :term:`gff` output from buildGff function

    outfile : string
        A :term:`txt` file containing results'''

    infile, gfffile = infiles
    ps = pythonScriptsDir
    if BamTools.isPaired(infile):
        paired = "yes"
    else:
        paired = "no"
    strandedness = PARAMS["DEXSeq_strandedness"]

    statement = '''python %(ps)s/dexseq_count.py
    -p %(paired)s
    -s %(strandedness)s
    -r pos
    -f bam  %(gfffile)s %(infile)s %(outfile)s'''
    P.run()


@collate(countDEXSeq,
         regex("counts.dir/([^.]+)\.txt"),
         r"summarycounts.tsv")
def aggregateExonCounts(infiles, outfile):
    ''' Build a matrix of counts with exons and tracks dimensions.

    Uses `combine_tables.py` to combine all the `txt` files output from
    countDEXSeq into a single :term:`tsv` file named
    "summarycounts.tsv". A `.log` file is also produced.

    Parameters
    ---------
    infiles : list
        a list of `tsv.gz` files from the feature_counts.dir that were the
        output from feature counts
    outfile : string
        a filename denoting the file containing a matrix of counts with genes
        as rows and tracks as the columns - this is a `tsv.gz` file      '''

    infiles = " ".join(infiles)
    statement = '''python %(scriptsdir)s/combine_tables.py
    --columns=1
    --take=2
    --use-file-prefix
    --regex-filename='([^.]+)\.txt'
    --log=%(outfile)s.log
    %(infiles)s
    | sed 's/geneid/gene_id/'
    > %(outfile)s '''

    P.run()


@follows(buildGtf)
@mkdir("results.dir/rMATS")
@subdivide(["%s.design.tsv" % x.asFile().lower() for x in DESIGNS],
           regex("(\S+).design.tsv"),
           r"results.dir/rMATS/\1.dir")
def runMATS(infile, outfile):
    '''run rMATS.'''

    if not os.path.exists(outfile):
        os.makedirs(outfile)

    gtffile = os.path.abspath("geneset.gtf")

    design = Expression.ExperimentalDesign(infile)

    if len(design.groups) != 2:
        raise ValueError("Please specify exactly two groups per experiment.")

    # job_threads = PARAMS["MATS_threads"]
    # job_memory = PARAMS["MATS_memory"]

    m = PipelineSplicing.rMATS(gtf=gtffile, design=design,
                               pvalue=PARAMS["MATS_cutoff"])

    statement = m.build(outfile)

    P.run()


@follows(countDEXSeq)
@mkdir("results.dir/DEXSeq")
@subdivide(["%s.design.tsv" % x.asFile().lower() for x in DESIGNS],
           regex("(\S+).txt"),
           r"results.dir/DEXSeq/\1.dir")
def runDEXSeq(infile, outfile):
    '''
    '''
    if not design.has_replicates and dispersion is None:
        raise ValueError("no replicates and no dispersion")

    if not os.path.exists(outfile):
        os.makedirs(outfile)

    gfffile = os.path.abspath("geneset.gff")

    design = Expression.ExperimentalDesign(infile)

    if len(design.groups) != 2:
        raise ValueError("Please specify exactly two groups per experiment.")

    # job_threads = PARAMS["MATS_threads"]
    # job_memory = PARAMS["MATS_memory"]

    m = PipelineSplicing.DEXSeq(gtf=gfffile, design=design, counts=counts,
                                model=PARAMS["DEXSeq_model"])

    statement = m.build(outfile)

    P.run()


@follows(runMATS)
def full():
    pass


@follows(mkdir("report"))
def build_report():
    '''build report from scratch.

    Any existing report will be overwritten.
    '''

    E.info("starting report build process from scratch")
    P.run_report(clean=True)


@follows(mkdir("report"))
def update_report():
    '''update report.

    This will update a report with any changes inside the report
    document or code. Note that updates to the data will not cause
    relevant sections to be updated. Use the cgatreport-clean utility
    first.
    '''

    E.info("updating report")
    P.run_report(clean=False)


@follows(update_report)
def publish_report():
    '''publish report in the CGAT downloads directory.'''

    E.info("publishing report")
    P.publish_report()

if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
