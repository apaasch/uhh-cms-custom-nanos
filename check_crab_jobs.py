import os
import sys
import json
import yaml

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from tqdm import tqdm
from itertools import chain
from collections.abc import Iterable
from subprocess import PIPE, Popen
import numpy as np

try:
    import gfal2
except ImportError as e:
    print("WARNING: could not import gfal2")
    print(e)
    print("gfal will be disabled!")
    gfal2 = None

# setup dbs search
try:
    from dbs.apis.dbsClient import DbsApi
    cms_dbs_url = "https://cmsweb.cern.ch/dbs/prod/global/DBSReader"
    api = DbsApi(url=cms_dbs_url)
except:
    print("WARNING: Could not find dbs3 module. Did you install it with")
    print("python3 -m pip install --user dbs3-client")
    print("?")
    print("Will use dasgoclient as fallback instead")
    api = None

wlcg_template= os.path.join("{wlcg_prefix}{wlcg_dir}",
    "{sample_name}",
    "{crab_dirname}",
    "{time_stamp}",
)
verbosity=0

def load_remote_output(
    wlcg_path: str,
) -> list[str]:
    """Function to load file paths from a remote WLCG target *wlcg_path*.
    First, the function checks for the gfal2 module. If gfal is loaded correctly,
    the list of files from the remote directly *wlcg_path* is loaded.
    If any of these steps fail, an empty list is returned

    Args:
        wlcg_path (str):    Path to the WLCG remote target, which consists of the
                            WLCG prefix and the actual directory on the remote
                            site (constructed from global wlcg_template)

    Returns:
        list[str]:  List of files in remote target *wlcg_path*. Defaults to
                    empty list.
    """
    # check for gfal
    if not gfal2:
        print("gfal2 was not imported, skipping this part")
        return []
    # This next part fails if the remote target does not exist, so wrap
    # it with try
    try:
        # create gfal context
        ctx = gfal2.creat_context()
        # load list of files
        filelist = ctx.listdir(wlcg_path)
        return [os.path.join(wlcg_path, x) for x in filelist]
    except Exception as e:
        if verbosity >= 1:
            print(f"unable to load files from {wlcg_path}, skipping")
            from IPython import embed; embed()
        return []

def check_job_outputs(
    collector_set: set[str],
    input_map: dict[str, list[str]],
    job_details: dict[str, dict],
    state: str="failed",
    job_outputs: set or None=None,
) -> None:
    """Function to collect information about jobs in *job_details*.
    First, all job ids with state *state* are retrieved from *job_details*.
    Then, the *collector_set* is filled with information depending on the 
    *state*.
    If *state* is 'failed', the *collector_set* is filled with paths to files
    that correspond to failed jobs and thus should not exist.
    If *state* is 'finished', the *collector_set* is filled with lfns that
    were successfully processed. In this case, an additional check whether
    a lfn is already marked as done is performed, and raises an error if
    a marked lfn is supposed to be added again.
    If the set *job_outputs* is neither None nor empty, the file paths are
    matched to the job ids with the current state. Only IDs with output files
    are considered further.

    Args:
        collector_set (set):    set to be filled with information, depending on 
                                *state* (see description above)
        input_map (dict): Dictionary of format {job_id: list_of_lfns}
        job_details (dict): Dictionary containing the status of the jobs of
                            format {job_id: ADDITIONAL_INFORMATION}.
                            *ADDITIONAL_INFORMATION* is a dict that must contain
                            the keyword 'State'.
        state (str, optional):  State to select the relevant job ids.
                                Must be either "failed" or "finished".
                                Defaults to "failed".
        job_outputs (set, optional):    if a set of output files is given,
                                        only job ids with output files are
                                        considered as relevant. Defaults to None

    Raises:
        ValueError: If a lfn is already marked as done but is associated with
                    a done job again, the ValueError is raised.
    """
    relevant_ids = set(filter(
        lambda x: job_details[x]["State"] == state,
        job_details
    ))

    # if there are paths to the job outputs available, only select ids that
    # actually have an output
    if isinstance(job_outputs, set) and not len(job_outputs) == 0:
        relevant_ids = set(filter(
            lambda x: any(path.endswith(f"nano_{x}.root") for path in job_outputs), 
            relevant_ids
        ))

    # for state "failed", collect output files that should not be there
    if state == "failed":
        collector_set.update(filter(
            lambda x: any(x.endswith(f"nano_{id}.root") for id in relevant_ids), 
            job_outputs
        ))
    # if state is finished, safe the done lfns (if the output of the job is also 
    # available)
    elif state == "finished":
        lfns = set()
        
        # first check if a lfn is already marked as done - this should not happen
        lfns = set(chain.from_iterable([input_map[x] for x in relevant_ids]))

        overlap = collector_set.intersection(lfns)
        if len(overlap) != 0:
            if verbosity == 0:
                msg = " ".join(f"""
                    {len(overlap)} LFNs are already marked as 'done' but
                    have come up here again. This should not happen
                """.split())
                raise ValueError(msg)
            else:
                overlap_string = "\n".join(overlap)
                raise ValueError(f"""
                The following lfns were already marked as done:
                {overlap_string}

                This should not happen!
                """)
            
        collector_set.update(lfns)

def get_job_inputs(crab_dir: str, job_input_file: str="job_input_files.json"):
    """Load job input file. This .json file contains the mapping of the form
    {
        'job_id': [
            'lfn_1',
            'lfn_2',
            ...
        ],
        ...
    }

    Args:
        crab_dir (str): current crab directory
        job_input_file (str, optional): file name in *crab_dir* containing the
                                        mapping. 
                                        Defaults to "job_input_files.json".
    
    Raises:
        ValueError: If the file containing the file mapping does not exist

    Returns:
        dict: file mapping of format described above
    """    

    # first, build file path
    path = os.path.join(crab_dir, job_input_file)

    # if the file does not exist, raise an error
    if not os.path.exists(path):
        print(f"Could not load input mapping from file '{path}'")
        return None

    # load json file
    with open(path) as f:
        input_map = json.load(f)

    return input_map

def get_status(sample_dir: str, status_file: str, crab_dir: str) -> dict[str, dict]:
    """Function to load the status from a .json file in *sample_dir*.
    If the file 'sample_dir/status_file.json' does not exist, the script falls
    back to loading 'status.json'

    Args:
        sample_dir (str): path to directory containing the status json file
        status_file (str): name of the .json file containing the job stati
        crab_dir (str): path to current crab base directory - currently not used

    Raises:
        NotImplementedError:    If the job stati cannot be loaded from a json
                                file they must be obtained with `crab status`
                                directoy. This is not implemented at the moment


    Returns:
        dict: Dictionary with status information given by crab
    """    

    # build path to the file containing the status information
    status_path = os.path.join(sample_dir, f"{status_file}.json")

    # also build a fallback in case the current file does not exist
    backup_status_path = os.path.join(sample_dir, "status.json")

    # now check if the file in either *status_path* or the backup exists
    if os.path.exists(status_path):
        with open(status_path) as f:
            status = json.load(f)
    elif os.path.exists(backup_status_path):
        with open(backup_status_path) as f:
            status = json.load(f)
    else:
        # if they do not exist, raise an error
        raise NotImplementedError("Obtaining the status from crab not implemented yet!")
    
    return status

def check_status(status: dict[str, dict], crab_dir: str) -> None:
    """Function to ensure that the *status* dictionary indeed belongs to the
    current crab base directory *crab_dir*. To this end, the 'project_dir'
    in the status is compared to the absolute path of *crab_dir*.

    Args:
        status (dict): Dictionary containing status information about the jobs
        crab_dir (str): path to current crab base directory

    Raises:
        ValueError: if the 'project_dir' does not match the absolute path of
                    the current crab base directory, an error is raised
    """    
    # load project dir from status
    status_project_dir = status.get("project_dir", None)

    # build absolute path for *crab_dir*
    abs_crab_dir = os.path.abspath(crab_dir)

    # do the comparison
    if not status_project_dir or not status_project_dir == abs_crab_dir:
        msg = "Project dir in status file does not match current crab dir under scutiny!"
        raise ValueError(msg)

def load_das_key(sample_name: str, sample_config: str) -> str or None:
    """Small function to extract the DAS key for sample *sample_name* 
    from the *sample_config*. First, the *sample_config*
    is opened (has to be in yaml format!). Afterwards, the entry *sample_name*
    is extracted. This entry should be a dictionary itself, which should contain
    the key 'miniAOD' with the DAS key for this sample.

    Args:
        sample_name (str): Name of the sample as provided in the sample config
        sample_config (str):    path to the sample_config.yaml file containing 
                                the information mentioned above.

    Returns:
        str or None: If successfull, this function returns the DAS key, else None
    """    
    das_key = None
    # open the sample config
    with open(sample_config) as f:
        sample_dict = yaml.load(f, yaml.Loader)

    # look up information for sample_name
    sample_info = sample_dict.get(sample_name, dict())
    # if there is no sample information, exit here
    if len(sample_info) == 0:
        if verbosity >= 1:
            print(f"WARNING: Unable to load information for sample '{sample_name}'")
        return das_key
    return sample_info.get("miniAOD", None)

def get_campaign_name(das_key: str=None) -> str:
    """small function to translate the sample name attributed by the 
    crabOverseer to the original MC campaign name. The original 
    campaign name is then extracted from the DAS key. If any of these steps
    fails, the fucntion returns an empty string

    Args:
        das_key (str):  DAS key in str format. Any other format will return
                        the default value of '""'.

    Returns:
        str: if successful, returns the original campaign name, else ""
    """    
    sample_campaign = ""
    # load information about the original miniAOD DAS key
    if not isinstance(das_key, str):
        if verbosity >= 1:
            msg=" ".join(f"""
            WARNING: Unable to load campaign name from das key of type
            '{type(das_key)}'
            """.split())
            print(msg)
        return sample_campaign

    # original campaign name is the first part of the DAS key
    sample_campaign = das_key.split("/")[1]
    
    return sample_campaign

def check_crab_directory(
    sample_dir: str,
    sample_name: str,
    suffix: str,
    das_key: str,
    status_file: str,
    known_lfns: set[str],
    done_lfns: set[str],
    failed_job_outputs: set[str],
    pbar: Iterable,
    wlcg_dir: str,
    wlcg_prefix: str,
    job_input_file: str="job_input_files.json",
) -> None:
    """Function to check a specific crab base directory in *sample_dir*.
    First, the name of the crab base directory (*crab_dir*) is built from
    *sample_dir*, *sample_name* and *suffix*.
    Then, the input file list and the status of the jobs of *crab_dir* is loaded.
    For book-keeping purposes, a set of known lfns *known_lfns* is also required.
    If this set is empty (which should happen for the first crab base directory
    that (should) consider all lfns), fill this set.
    If there are unknown lfns in the other (recovery) jobs, raise an error.
    Additionally, obtain the time stamp of the current crab job to correctly
    load/check the output of the jobs on the WLCG site.
    Finally, the relevant information about failed and finished jobs is obtained.

    Args:
        sample_dir (str): path to the directory containing the crab base directories
        sample_name (str): Name of the current sample (as it is in sample_dir).
                            Used to build name of crab directory via 'crab_*sample_name*'
        sample_campaing (str):  Name of the official campaign for sample 
                                *sample_name*. Can be obtained with 
                                meth::`parse_sample_name` based on the
                                sample_name and the sample_config.yaml file
        suffix (str):   suffix to use for the current crab directory, such that 
                        the final name of the *crab_dir* is 
                        'crab_*sample_name*_*suffix*'
        status_file (str):  name of the status file containing the information 
                            for the current crab base directory *crab_dir* 
        known_lfns (set[str]): Set of already known lfns
        done_lfns (set[str]): Set of lfns corresponding to successful jobs
        failed_job_outputs (set[str]):  Set of output files originating from 
                                        failed jobs. This should not happen,
                                        thus we track them
        pbar (Iterable): Status bar for the iteration in the main function.
                                Used to correctly display the current *crab_dir*.
        wlcg_dir (str): Name of the WLCG Directory containing the outputs 
                        of the jobs.
        wlcg_prefix (str): Prefix to contact the WLCG Directory with gfal
        job_input_file (str, optional): Name of the file containing the
                                        mapping of job_id -> input file(s) for
                                        a given *crab_dir*. 
                                        Defaults to "job_input_files.json".

    Raises:
        ValueError: If previously unkown lfns are encountered
        ValueError: if the time stamp of a given crab job cannot be obtained
    """    
    # build name of current crab base directory
    if not suffix == "" and not suffix.startswith("_"):
        suffix = "_"+suffix
    crab_dirname = f"crab_{sample_name}"+suffix
    crab_dir = os.path.join(sample_dir, crab_dirname)
    if not os.path.exists(crab_dir):
        if verbosity >= 1:
            print(f"Directory {crab_dir} does not exist, will stop looking here")
        return
    pbar.set_description(f"Checking directory {crab_dir}")
    
    # load the input file mapping of the form 'job_ids' -> list of lfns
    input_map = get_job_inputs(crab_dir=crab_dir, job_input_file=job_input_file)
    
    if not input_map:
        if verbosity >= 1:
            print(f"WARNING: could not load input map for directory {crab_dir}")
        return
    # perform sanity checks
    # first load a flat list of lfns
    flat_lfns = set(chain.from_iterable(input_map.values()))

    # if we don't know any lfns yet, use this set as a baseline
    if len(known_lfns) == 0:
        known_lfns.update(flat_lfns)
    
    # check if all lfns are known at this point
    unknown_lfns = flat_lfns.difference(known_lfns)
    if len(unknown_lfns) != 0:
        # if we enter here, lfns appeared that were previously unknown
        # this shouldn't be possible (unless maybe due to TAPE_RECALLS)
        # currently being checked
        msg = ""
        if verbosity == 0:
            msg = f"""
            Encountered {len(unknown_lfns)} while processing dir '{crab_dir}'
            This is not expected. For more information, use higher level of
            verbosity.
            """
            # raise ValueError(msg)
            
        else:
            unknown_lfns_string = "\n".join(unknown_lfns)
            from IPython import embed; embed()
            # raise ValueError(f"""
            #     Following lfns are not known in '{crab_dir}':
            #     {unknown_lfns_string}

            #     This should not happen!
            #     """)
        known_lfns.update(flat_lfns)

    # load the dictionary containing the job stati
    status = get_status(
        sample_dir=sample_dir,
        status_file=status_file,
        crab_dir=crab_dir
    )
    # sanity check whether we indeed loaded the correct status file
    check_status(status=status, crab_dir=crab_dir)

    # get general information about jobs
    n_jobs = status.get("n_jobs_total", 0)

    # load the information about the specific jobs that form the crab job
    job_details = status.get("details", dict())

    # load time stamp
    time_stamp = status.get("task_name", None)
    if not time_stamp:
        raise ValueError("Could not retrieve time stamp from status json!")
    time_stamp = time_stamp.split(":")[0]

    this_wlcg_template = wlcg_template.format(
        wlcg_prefix=wlcg_prefix,
        wlcg_dir=wlcg_dir,
        sample_name=get_campaign_name(das_key=das_key),
        crab_dirname=crab_dirname,
        time_stamp=time_stamp
    )

    # create complete path on remote WLCG system to output file
    # crab arranges the output files in blocks depending on the job id
    
    # get maximum ID to identify maximum block number later
    max_jobid = np.max([int(x) for x in job_details.keys()])
    # from IPython import embed; embed()
    # initialize set of outputs
    job_outputs = set()
    # get the maximum block number and iterate through the blocks
    pbar_blocks = tqdm(range(int(max_jobid/1000)+1))
    for i in pbar_blocks:
        pbar_blocks.set_description(f"Loading outputs for block {i:04d}")
        job_outputs.update(
            load_remote_output(
                wlcg_path=os.path.join(this_wlcg_template, f"{i:04d}")
            )
        )

    # load information about failed jobs
    check_job_outputs(
        job_outputs=job_outputs,
        collector_set=failed_job_outputs,
        input_map=input_map,
        job_details=job_details,
        state="failed",
    )

    # load information about failed jobs
    check_job_outputs(
        job_outputs=job_outputs,
        collector_set=done_lfns,
        input_map=input_map,
        job_details=job_details,
        state="finished",
    )

def get_dbs_lfns(das_key: str) -> set[str]:
    """Small function to load complete list of valid LFNs for dataset with
    DAS key *das_key*. Only files where the flag 'is_file_valid' is True
    are considered. Returns set of lfn paths if successful, else an empty set

    Args:
        das_key (str): key in CMS DBS service for the dataset of interest

    Returns:
        set[str]: Set of LFN paths
    """    
    # initialize output set as empty
    output_set = set()

    # if the api for the dbs interface was initialized sucessfully, we can
    # load the files
    if api:
        # load the file list for this dataset
        file_list = api.listFiles(dataset=das_key, detail=1)
        # by default, this list contains _all_ files (also LFNs that are not
        # reachable) so filter out broken files
        file_list = list(filter(
            lambda x: x["is_file_valid"] == True,
            file_list
        ))
        output_set = set([x["logical_file_name"] for x in file_list])
    return output_set 

def get_das_information(
    das_key: str,
    relevant_info: str="num_file",
    default: int=-1,
) -> int:
    allowed_modes="file_size num_event num_file".split()
    if not relevant_info in allowed_modes:
        raise ValueError(f"""Could not load information '{relevant_info}'
        because it's not part of the allowed modes: file_size, num_event, num_file
        """)
    output_value = default

    # execute DAS query for sample with *das_key*
    process = Popen(
        [f"dasgoclient --query '{das_key}' -json"], 
        shell=True, stdin=PIPE, stdout=PIPE
    )
    # load output of query
    output, stderr = process.communicate()
    # from IPython import embed; embed()
    # output is a string of form list(dict()) and can be parsed with
    # the json module
    try:
        das_infos = json.loads(output)
    except Exception as e:
        # something went wrong in the parsing, so just return the default
        return output_value
    # not all dicts have the same (relevant) information, so go look for the
    # correct entry in list. Relevant information for us is the total
    # number of LFNs 'nfiles'
    relevant_values = list(set(
        y.get(relevant_info) 
        # only consider entries in DAS info list with dataset information
        for x in das_infos if "dataset" in x.keys() 
        # only consider those elements in dataset info that also have 
        # the relevant information
        for y in x["dataset"] if relevant_info in y.keys()
    ))
    # if this set is has more than 1 or zero entries, something went wrong
    # when obtaining the relevant information, so return the default value

    # if the set has exactly one entry, we were able to extract the relevant
    # information, so return it accordingly
    if len(relevant_values) == 1:
        output_value = relevant_values[0]
    return output_value

def main(*args,
    sample_dirs=[],
    wlcg_dir=None,
    wlcg_prefix="",
    suffices=[],
    status_files=[],
    sample_config=None,
    dump_filelists=False,
    **kwargs
):
    """main function. Load information provided by the ArgumentParser. Loops
    Thorugh the sample directories provided as *sample_dirs* and the *suffices*
    to check the individual crab base directories.
    Finally, check if any lfns are unaccounted for in the list of finished jobs.
    """  
    # load the information from the argument parser  
    meta_infos = dict()

    # loop through the sample directories containing the crab base directories
    pbar_sampledirs = tqdm(sample_dirs)
    for sample_dir in pbar_sampledirs:
        # if a sample dir does not exist, no need to check it
        if not os.path.exists(sample_dir):
            print(f"Directory {sample_dir} does not exist, skipping!")
            continue
        sample_dir = sample_dir.strip(os.path.sep)

        # from IPython import embed; embed()
        # extract the sample name from the sample directory
        sample_name=os.path.basename(sample_dir)
        pbar_sampledirs.set_description(f"Checking sample {sample_name}")
        das_key = load_das_key(
            sample_name=sample_name, sample_config=sample_config
        )

        # get full set of lfns for this sample
        known_lfns=get_dbs_lfns(das_key=das_key)   

        # if the dbs could not be contacted for some reason, use DAS
        # to load the total number of LFNS
        if len(known_lfns) > 0:
            n_total = len(known_lfns)
        else:
            # get total number of LFNs from DAS
            n_total = get_das_information(das_key=das_key)

        # set up the sets to keep track of the lfns
        done_lfns=set()     # set of lfns processed by successful jobs
        
        # set of **outputs** from failed jobs, which shouldn't happen
        failed_job_outputs=set()    

        # loop through suffices to load the respective crab base directories
        pbar_suffix = tqdm(zip(suffices, status_files))
        for suffix, status_file in pbar_suffix:
            check_crab_directory(
                sample_dir=sample_dir,
                sample_name=sample_name,
                suffix=suffix,
                status_file=status_file,
                known_lfns=known_lfns,
                done_lfns=done_lfns,
                failed_job_outputs=failed_job_outputs,
                pbar=pbar_suffix,
                wlcg_dir=wlcg_dir,
                wlcg_prefix=wlcg_prefix,
                das_key=das_key,
            )

        # in the end, all LFNs should be accounted for
        unprocessed_lfns = known_lfns.symmetric_difference(done_lfns)

        sample_dict = dict()
        sample_dict["das_total"] = n_total
        sample_dict["total"] = len(known_lfns)
        sample_dict["done"] = len(done_lfns)
        sample_dict["outputs from failed jobs"] = len(failed_job_outputs)
        sample_dict["missing"] = len(unprocessed_lfns)
        if dump_filelists:
            sample_dict["total_lfns"] = list(known_lfns.copy())
            sample_dict["done_lfns"] = list(done_lfns.copy())
            sample_dict["missing_lfns"] = list(unprocessed_lfns.copy())
            sample_dict["failed_outputs"] = list(failed_job_outputs.copy())
        meta_infos[sample_name] = sample_dict.copy()
        
        if verbosity >= 1:
            if len(failed_job_outputs) != 0:
                print("WARNING: found job outputs that should not be there")
                print(f"Sample: {sample_dir}")
                for f in failed_job_outputs:
                    print(f)
            if len(unprocessed_lfns) != 0:
                print(f"WARNING: following LFNs for sample {sample_dir} were not processed!")
                for f in unprocessed_lfns:
                    print(f)
    
    # do some final sanity check: if we found missing lfns, print them here
    # so the user can do something
    build_meta_info_table(meta_infos=meta_infos)

    samples_with_missing_lfns = list(filter(
        lambda x: meta_infos[x]["missing"] != 0 or 
                   ( meta_infos[x]["das_total"] != meta_infos[x]["total"]
                        and meta_infos[x]["das_total"] != -1
                    ), 
        meta_infos
    ))
    if len(samples_with_missing_lfns) != 0:
        print("\n\n")
        print("Samples with missing LFNS:")
        build_meta_info_table(
            meta_infos={x: meta_infos[x] for x in samples_with_missing_lfns},
            outfilename="samples_with_missing_lfns.json"
        )

def build_meta_info_table(
    meta_infos: dict,
    outfilename: str="crab_job_summary.json"
) -> None:
    """Helper function to convert collected information of crab jobs into
    human-readable table. The information is safed in *meta_infos*, which
    is a dictionary of the format
    {
        sample_name: {
            "das_total": TOTAL_NUMBER_OF_LFNS_IN_DAS
            "total": TOTAL_NUMBER_OF_LFNS,
            "done": NUMBER_OF_DONE_LFNS,
            "missing": NUMBER_OF_MISSING_LFNS,
            "outputs from failed jobs": NUMBER_OF_OUTPUTS_FROM_FAILED_JOBS
        }
    }

    Current supported formats: markdown (md)

    Args:
        meta_infos (dict): Dictionary containing above mentioned information
    """    

    # first build the header of the table
    headerparts = ["{: ^16}".format(x) 
                for x in ["Sample", "DAS Total #LFNs", "Total #LFNs", 
                            "Done #LFNs", "Missing #LFNs", 
                            "#Outputs from failed",
                        ]
            ]
    lines = ["| {} |".format(" | ".join(headerparts))]
    # markdown tables have a separation line with one '---' per column
    lines += ["| {} |".format(" | ".join(["---"]*len(headerparts)))]
    # loop through the samples that were processed
    for s in meta_infos:
        # load information for table
        das_tot = meta_infos[s]["das_total"]
        tot = meta_infos[s]["total"]
        done = meta_infos[s]["done"]
        missing = meta_infos[s]["missing"]
        failed = meta_infos[s]["outputs from failed jobs"]
        # create line for table
        lines.append("| {} |".format(" | ".join(
                ["{: ^16}".format(x) 
                    for x in [s, das_tot, tot, done, missing, failed ]
                ]
            )
            )
        )
    # create final table
    table = "\n".join(lines)
    print(table)
    with open(outfilename, "w") as f:
        json.dump(meta_infos, f, indent=4)


def parse_arguments():
    description = """
    Small script to cross check crab jobs. This script checks the following:
    - Was every LFN entry in DAS processed?
    - Are there outputs for all jobs with status "DONE"?
    - Are there outputs for jobs with status "FAILED"?
    - Is there an output for every LFN?

    The script expects the following directory structure:
    - SAMPLE_NAME
    |   ---crab_SAMPLE_NAME
    |   ---crab_SAMPLE_NAME_recovery_1
    |   ---crab_SAMPLE_NAME_recovery_2
    |   ---...
    - NEXT_SAMPLE
    ....

    This structure is created by the crabOverseer by default.
    You can specify which suffixes (e.g. *recovery_1*) are to be checked, 
    see options below. Note that the order you specify in this option is also
    the order in which the directories are checked.

    If you want to check the outputs of the jobs on the (remote) target site,
    make sure that gfal2 is available to python. On lxplus, you can do this
    by first using

    source "/cvmfs/grid.cern.ch/centos7-ui-160522/etc/profile.d/setup-c7-ui-python3-example.sh" ""

    Note that this only works on SLC7 machines at the moment, not on CentOS 8.
    """
    usage = "python %(prog)s [options] path/to/directories/containting/crab_base_dirs"


    parser = ArgumentParser(
        # usage=usage,
        description=description,
        formatter_class=RawDescriptionHelpFormatter)

    parser.add_argument(
        "-w", "--wlcg-dir",
        help=" ".join("""
            path to your WLCG directory that is the final destination for your
            crab jobs. On T2_DESY, this would be your DCACHE (/pnfs) directory
        """.split()),
        metavar="PATH/TO/YOUR/WLCG/DIRECTORY",
        type=str,
        default=None,
        required=True,
        dest="wlcg_dir",
    )
    parser.add_argument(
        "--wlcg-prefix",
        help=" ".join(
            """
                Prefix to contact the WLCG directory at the remote site.
                Defaults to prefix for T2_DESY 
                (srm://dcache-se-cms.desy.de:8443/srm/managerv2?SFN=)
            """.split()
        ),
        type=str,
        default="srm://dcache-se-cms.desy.de:8443/srm/managerv2?SFN=",
        dest="wlcg_prefix"
    )
    parser.add_argument(
        "-s", "--suffices",
        help=" ".join("""
            specify the suffices you would like for the check of the crab
            source directory. Can be a list of suffices, e.g. 
            `-s "" recovery_1 recovery_2`. Defaults to 
            ["", "recovery_1", "recovery_2`"]
        """.split()),
        default=None,
        nargs="+",
    )
    parser.add_argument(
        "--status-files",
        help=" ".join(
            """
            List of status files containing job information.
            Entries in this list are zipped to the list of suffixes (option `-s`)
            Therefore, the length of this list must be the same as list
            obtained from option `--suffix`! 
            Defaults to ["status_0", "status_1", "status"]
            """.split()
        ),
        default=None,
        nargs="+",
        dest="status_files"
    )

    parser.add_argument(
        "--sample-config", "-c",
        help=" ".join(
            """
            path to sample config that contains the information about the
            original miniaod files (and thus the original name of the sample).
            Config must be the in yaml format!
            """.split()
        ),
        default=None,
        required=True,
        dest="sample_config",
        metavar="path/to/sample_config.yaml"
    )

    parser.add_argument(
        "--dump-filelists",
        help=" ".join(
            """
            save the paths to all lfns, the done lfns, the missing lfns
            and to outputs on the WLCG remote site from failed jobs
            in the final summary .json file. Defaults to False
            """.split()
        ),
        default=False,
        action="store_true",
        dest="dump_filelists",
    )

    parser.add_argument(
        "sample_dirs",
        help=" ".join("""
            Path to sample diectories containing the crab base directories
            (see description).
        """.split()),
        metavar="PATH/TO/SAMPLE/DIRECTORIES",
        type=str,
        nargs="+",
    )

    parser.add_argument(
        "-v", "--verbosity",
        type=int,
        default=0,
        help=" ".join(
            """
                control the verbosity of the output. Currently implemented levels
                0: just print number of files/outputs for each sample
                1: actually print the paths for the different files
            """.split()
        )
    )

    args = parser.parse_args()
    if args.suffices == None:
        args.suffices = ["", "recovery_1", "recovery_2"]

    if args.status_files == None:
        args.status_files = ["status_0", "status_1", "status"]
    
    if not os.path.exists(args.sample_config):
        parser.error(f"file {args.sample_config} does not exist!")
    
    global verbosity
    verbosity = args.verbosity
    return args

if __name__ == '__main__':
    args = parse_arguments()
    main(**vars(args))