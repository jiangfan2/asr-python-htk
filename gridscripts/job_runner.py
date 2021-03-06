#!/usr/bin/env python2.6

########################################################################
# job_runner.py
#
# Description:
# ------------
# This script will run an array job in the appropriate way. 
# The appropriate way is:
# - If run on the stimulus host it will submit the command with qsub
# - If run on the triton host it will submit the command with sbatch
# - Otherwise it will spawn threads to process the commands locally
#
# Type job_runner.py --help to get a list of options.
#
#
# Author: Peter Smit (peter@cis.hut.fi)
########################################################################
import sys
import os.path
import signal
from subprocess import *
from optparse import OptionParser
import re
import time
from socket import gethostname
import random

# The following modules are only needed when we run locally. Ignore if they are not present.
try:
    from multiprocessing import Queue
    from Queue import Empty

    import multiprocessing
    from multiprocessing import Process
except ImportError:
    pass


#global variables
verbosity = 0
runner = None

#default_options is only used when job_runner is used as module
default_options = {}

def submit_job(commandarr, extra_options = 1):
    global runner
    global default_options
    global verbosity
    
    if type(extra_options).__name__ == 'dict':
        input_options = dict(default_options, **extra_options)
    else:
        input_options = dict(default_options)
        input_options['numtasks'] = extra_options
    
    parser = getOptParser()
    options = parser.parse_args(["dummy"])[0]
    
    for a,b in input_options.items():
        setattr(options,a,b)
    
    verbosity = options.verbosity
    
    setNewRunner(options, commandarr)
    
    runner.run()
    

def main():
    global verbosity
    global runner
    
    #define command line options
    parser = getOptParser()
    
    (options, args) = parser.parse_args()
    verbosity = options.verbosity
    
    #check or at least a command is given
    if len(args) == 0:
        print "No command given!"
        sys.exit(10)
    
    setNewRunner(options, args)
    
    # Run our task
    runner.run()

def getOptParser():
    usage = "usage: %prog [options] -- command"
    parser = OptionParser(usage=usage)
    parser.add_option("-T", "--numtasks", type="int", dest="numtasks", help="Number of tasks to launch", default=1)
    parser.add_option("-t", "--timelimit", dest="timelimit", help="Timelimit for one task (in hh:mm:ss format)", default="00:15:00")
    parser.add_option("-m", "--memlimit", type="int", dest="memlimit", help="Memorylimit for one task (in MB)", default=100)
    parser.add_option("-o", "--output-stream", dest="ostream", help="write outputstream to FILE (%c for command, %j for id of first job, %J for real job id, %t for task id). If a directory is given, the default format is used in that directory", default="%c.o%j.%t", metavar="FILE")
    parser.add_option("-e", "--error-stream", dest="estream", help="write outputstream to FILE (%c for command, %j for id of first job, %J for real job id, %t for task id). If a directory is given, the default format is used in that directory", default="%c.e%j.%t", metavar="FILE")
    parser.add_option("-p", "--priority", type="int", dest="priority", help="Job priority. Higher priority is running later", default=0)
    parser.add_option("-q", "--queue", dest="queue", help="Queue (ignored on triton if starts with -)", default="-soft -q helli.q")
    parser.add_option("-c", "--cores", type="int", dest="cores", help="Number of cores to use (when running local). Negative numbers indicate the number of cores to keep free", default=-1)
    parser.add_option("-N", "--nodes", type="int", dest="nodes", help="Number of nodes to use (Triton)", default=1)
    parser.add_option("-r", "--retrys", type="int", dest="retry", help="Number of retry's for failed jobs (just Triton for now)", default=3)
    parser.add_option("-V", "--verbosity", type="int", dest="verbosity", help="verbosity", default=0)
    parser.add_option("-x", "--exclude", dest="exclude_nodes", help="Triton nodes to be excluded", default="" )
    return parser

def setNewRunner(options, args):
    global runner
    
    # Select runner class based on hostname
    hostname = gethostname()
    if hostname[0:(len("stimulus"))] == "stimulus":
        runner = StimulusRunner(options, args)
    elif hostname[0:(len("triton"))] == "triton":
        runner = TritonRunner(options, args)
    else:
        runner = LocalRunner(options, args)

#Base class with common functionality. Do not use inmediately but a sub-class instead
class Runner(object):
    options = None
    commandarr = []
    jobname = ""
    verbosity = 0
    
    def __init__(self, options, commandarr):
        self.options = options
        self.verbosity = options.verbosity
        
        # Prepend full path if local script is given for the command and the output streams
        if commandarr[0][0] != '.' and commandarr[0][0] != '/' and os.path.isfile(os.getcwd() + '/' + commandarr[0]):
            commandarr[0] = os.getcwd() + '/' + commandarr[0]
        
        if self.options.ostream[0] != '.' and self.options.ostream[0] != '/':
            self.options.ostream = os.getcwd() + '/' + self.options.ostream
            
        if self.options.estream[0] != '.' and self.options.estream[0] != '/':
            self.options.estream = os.getcwd() + '/' + self.options.estream
        
        # If the outputstreams are just directories, add the default filename pattern
        if os.path.isdir(self.options.ostream):
            self.options.ostream = self.options.ostream + '/%c.o%j.%t'
            
        if os.path.isdir(self.options.estream):
            self.options.estream = self.options.estream + '/%c.e%j.%t'
            
        self.commandarr = commandarr
        
        #create a nice job name
        self.jobname = os.path.basename(commandarr[0])
        
        # do some validations
        self.validate_options()
        
    def validate_options(self):
        # check time limit
        if not re.match('^[0-9]{2}:[0-9]{2}:[0-9]{2}$', self.options.timelimit):
            print "Time limit has not the correct syntax (hh:mm:ss). For example 48:00:00 for 2 days!"
            sys.exit(10)
    
    # Method for replacing the %t %j %J %c flags. Used for both output streams and commands
    def replace_flags(self, pattern, task, jobid_l = None, jobid_u = None):
        ret = pattern
        
        if type(ret).__name__=='list':
            ret = [str(item).replace('%t', str(task)) for item in ret]
        else:
            ret = ret.replace('%c', self.jobname)
            ret = ret.replace('%t', str(task))
            
            if jobid_l is None:
                ret = ret.replace('%j', '%J')
            else:
                ret = ret.replace('%j', str(jobid_l))
            
            if jobid_u is not None:
                ret = ret.replace('%J', str(jobid_u))
        
        return ret

def time_limit_to_seconds(time_limit):
    hours, minutes, seconds = time_limit.split(':', 2)
    return int(seconds) + (int(minutes) * 60) + (int(hours) * 60 * 60)

# Logic for running on the stimulus cluster
class StimulusRunner(Runner):
    def __init__(self, options, commandarr):
        super(StimulusRunner,self).__init__(options, commandarr)
    
    def run(self):
        global verbosity

        # Construct the qsub command
        batchcommand=['qsub']
        
        # Give a jobname
        batchcommand.extend(['-N', self.jobname ])
        
        # Set the timelimit and memory limit
        batchcommand.extend(['-l', 'mem='+str(self.options.memlimit)+'M,t='+self.options.timelimit])
        batchcommand.extend(['-cwd'])
        
        # If people want to be nice, we set a priority (Stimulus sees negative priority as nice)
        if self.options.priority > 0:
            batchcommand.extend(['-p', str(-1 * self.options.priority)])
        
            
        # Construct the filenames for the error and output stream
        outfile = self.replace_flags(self.options.ostream, "$TASK_ID", "$JOB_ID", "$JOB_ID")
        errorfile = self.replace_flags(self.options.estream, "$TASK_ID", "$JOB_ID", "$JOB_ID")
        
        real_command = self.replace_flags(self.commandarr, "$SGE_TASK_ID")
        
        # Set number of tasks
        batchcommand.extend(['-t', '1-'+str(self.options.numtasks)])
        
        # Set output streams
        batchcommand.extend(['-o', outfile, '-e', errorfile])
        batchcommand.extend(['-sync', 'y'])

        #Wrap it in a script file (Escaped)
        script = "#!/bin/bash\n" + "\"" + "\" \"".join(real_command) + "\""
        
        #Call the command. Feed in the script through STDIN and catch the result in output
        output = Popen(batchcommand, stdin=PIPE, stdout=PIPE).communicate(script)[0]
        
        if output.count("exited with exit code 0") < self.options.numtasks:
            if verbosity > 0:
                print str(output.count("exited with exit code 0")) + ' out of ' + str(self.options.numtasks) + ' tasks succeeded!'
            sys.exit(1)
        elif verbosity > 0:
            print 'All ' + str(self.options.numtasks) + ' tasks succeeded'
    
    def cancel(self):
        sys.exit(255)


# Logic for running on the Triton cluster
class TritonRunner(Runner):
    job = 0

    
    def __init__(self, options, commandarr):
        super(TritonRunner,self).__init__(options, commandarr)

    def get_exclude_list(self, exclude_list):
        new_list = exclude_list
        if len(exclude_list) > 0 and not exclude_list.endswith(','):
            new_list = new_list + ','

        list = []
        if os.path.exists('/home/smitp1/.bad_node_list'):
            for line in open('/home/smitp1/.bad_node_list'):
                list.append(line.rstrip())

        new_list = new_list + ','.join(list)
        return new_list


    def run(self):
        global verbosity

        retry_s = {}
        self.job = {}

        for task_num in range(1, self.options.numtasks+1):
            task_id = task_num
            if self.options.numtasks == 1:
                task_id = 'single'

            # Construct the sbatch command


            batchcommand=['sbatch']

            # Give a jobname
            batchcommand.extend(['-J', "%s.%s" %(self.jobname,task_id)])

            # Set the timelimit
            batchcommand.extend(['-t', self.options.timelimit])

            batchcommand.extend(['-N', str(1)])
            batchcommand.extend(['-n', str(1)])

            #exclude_nodes = self.options.exclude_nodes.split(',')
            real_exclude_list = self.get_exclude_list(self.options.exclude_nodes)
            if len(real_exclude_list) > 0:
                batchcommand.extend(['-x', real_exclude_list])


            # Set the memory limit
            batchcommand.append('--mem-per-cpu='+ str(self.options.memlimit))

            # If people want to be nice, we set a priority
            priority = self.options.priority

            outfile = self.replace_flags(self.options.ostream, task_id)
            errorfile = self.replace_flags(self.options.estream, task_id)

            batchcommand.extend(['-o', outfile])
            batchcommand.extend(['-e', errorfile])

            if not self.options.queue.startswith('-') and len(self.options.queue.rstrip()) > 0:
                batchcommand.extend(['-p', self.options.queue])
            elif time_limit_to_seconds(self.options.timelimit) <= time_limit_to_seconds('04:00:00'):
                batchcommand.extend(['-p', 'short'])
                priority = priority + 1

            # If people want to be nice, we set a priority
            if self.options.priority > 0:
                batchcommand.append('--nice='+str(self.options.priority))

            if task_id == 'single':
                real_command = self.replace_flags(self.commandarr, '1')
            else:
                priority = priority + 1
                real_command = self.replace_flags(self.commandarr, task_id)

            if priority > 0:
                batchcommand.append('--nice='+str(priority))

            job_id = self.submit_command(batchcommand, real_command)

            self.job[job_id]  = (batchcommand, real_command, task_id)
            retry_s[task_id] = 0

            time.sleep(self.delay * 1.0)

        self.print_submitted_jobs()

        all_success = True

        while len(self.job) > 0:
            Popen(['srun', '-t', '00:01:00', '-J', 'wait%s' % self.jobname, '-n', '1', '-N', '1', '-p', 'short', '--mem-per-cpu', '10', '--dependency=afterany:'+':'.join(self.job.keys()), 'sleep', str(0)], stderr=PIPE).wait()

            sacct_command = ['sacct', '--starttime', '1970-01-01', '--endtime', '2025-01-01', '-n', '--format=JobID,ExitCode,State', '-X', '-P', '-j', ','.join(self.job)]
            result = Popen(sacct_command, stdout=PIPE).communicate()[0]

            while result.count('RUNNING') > 0 or result.count('PENDING') > 0:
                print '.'
                time.sleep(2)

                result = Popen(sacct_command, stdout=PIPE).communicate()[0]

            statusses = result.split()

            for status in statusses:
                if '|' in status:
                    parts = status.split('|', 2)
                    if parts[1] != '0:0' or parts[2] != 'COMPLETED':
                        batch_command, real_command, task_id = self.job[parts[0]]
                        if retry_s[task_id] < self.options.retry:
                            job_id = self.submit_command(batch_command, real_command)
                            self.job[job_id] = (batch_command, real_command, task_id)
                            retry_s[task_id] += 1
                            print >> sys.stderr, "Retrying task %s" % task_id
                            time.sleep(1)
                        else:
                            print >> sys.stderr, "Task %s really failed" % task_id
                            all_success = False

                    del self.job[parts[0]]
            if len(self.job) > 0:
                self.print_submitted_jobs()

        if not all_success:
            sys.exit("Failed to do this")
        elif verbosity > 0:
            print 'All tasks succeeded'



    def print_submitted_jobs(self):
        jobs = sorted([int(k) for k in self.job.keys()])

        in_sequence = False
        prev_job = -1

        out_string = ''

        for job in jobs:
            if job != prev_job + 1:
                if in_sequence:
                    out_string += str(prev_job)
                    in_sequence = False
                out_string += ',%s' % job
            else:
                if not in_sequence:
                    in_sequence = True
                    out_string += '-'
            prev_job = job

        if in_sequence:
            out_string += str(prev_job)

        print "%s submitted as id's: %s" % (self.jobname, out_string[1:])

    delay = 1.0
    def submit_command(self, batch_command, real_command):
        if self.verbosity > 2:
            print >> sys.stderr, ' '.join(batch_command)
        if self.verbosity > 1:
            print >> sys.stderr, ' '.join(real_command)
        script = "#!/bin/bash\n" + "\"" + "\" \"".join(real_command) + "\""

        while True:
            #Call sbatch. Feed in the script through STDIN and catch the result in output
            output = Popen(batch_command, stdin=PIPE, stdout=PIPE).communicate(script)[0]

            #Find the jobid on the end of the line
            m = re.search('[0-9]+$', output)
            if m is not None:
                self.delay = max(self.delay *0.8, 2.0)
                return m.group(0)
            else:
                self.delay = self.delay * 1.5
                time.sleep(self.delay * 2.0)



    # Method for cancelling the Triton jobs
    def cancel(self):
        global verbosity
        cancelcommand=['scancel']
        if type(self.job).__name__=='dict':
            cancelcommand.extend(self.job.keys())
        else:
            cancelcommand.append(self.job)

        Popen([str(part) for part in cancelcommand], stderr=None, stdout=None).wait()
        if verbosity > 0:
            print 'Jobs are cancelled!'
        sys.exit(255)
        

# Class for running the command locally in multiple threads
class LocalRunner(Runner):
    job_id = 0
    num_cores = 1
    
    cancelled = False
    failed = False
    
    processes = []
    mainprocess = None

    
    def __init__(self, options, commandarr):
        super(LocalRunner,self).__init__(options, commandarr)

        
        if options.cores > 0:
            self.num_cores = options.cores
        else:
            self.num_cores = max(1, multiprocessing.cpu_count() + options.cores)
        
        if options.verbosity > 1:
            print str(self.num_cores) + " cores are used"
        
        # We choose a random job_id. What would be better?
        self.job_id = random.randint(1, 9999)
        
        if options.verbosity > 0:
            print "Job id "+str(self.job_id)
        
        self.mainprocress = multiprocessing.current_process()
        
    def run(self):
        global verbosity
        if verbosity > 0:
            print "Running job " + str(self.job_id) + " locally"
        
        q = Queue()
        
        # add all tasks to queue
        for t in range(1,self.options.numtasks + 1):
            outfile = self.replace_flags(self.options.ostream, t, self.job_id, self.job_id)
            errorfile = self.replace_flags(self.options.estream, t, self.job_id, self.job_id)
            real_command = self.replace_flags(self.commandarr, t, self.job_id, self.job_id)
            c = [t, real_command, outfile, errorfile]
            q.put(c)
            
        # make appropriate number of processes to process queue
        for pnum in range(1,self.num_cores+1):
            p = Process(target=self.runFromQueue, args=(q,))
            p.start()
            self.processes.append(p)
        
        # check or everything is ready (with processing)

        allready = False
        while not self.failed and not allready:
            allready = True
            for p in self.processes:
                if not p.is_alive():
                    p.join(5)
                if p.is_alive():
                    allready = False
        
        print "After ready"
        # If we stopped because a job has failed, cancel the other processes
        if self.failed:
            for p in self.processes:
                if p.is_alive():
                    p.terminate()
        
        # If failed return non-zero exit code
        if self.failed:
            sys.exit(1)
        
    def runFromQueue(self, q):
        global verbosity
        try:
            while not self.cancelled:
                command = q.get(True, 5)
                if self.verbosity > 1:
                    print "\tStart task " + str(command[0])
                of = open(command[2], 'w')
                ef = open(command[3], 'w')
                resultcode = Popen(command[1], stdout=of, stderr=ef).wait()
                of.close(); ef.close()
                
                if resultcode != 0:
                    print "Task " + str(command[0]) + " failed with code "+ str(resultcode) +"!"
                    self.cancelled = True
                    self.failed = True
                    sys.exit(1)
        except Empty:
            pass
        
    def cancel(self):
        if self.mainprocress == multiprocessing.current_process():
            self.cancelled = True
            try:
                for p in self.processes:
                    if p.is_alive():
                        p.terminate()
            except:
                pass


def signal_handler(signal, frame):
    global verbosity
    global runner
    
    print 'Signal received!'
    if runner is not None:
        runner.cancel()
    
    if verbosity > 0:
        print 'Jobs are cancelled!'
    sys.exit(255)
    
#Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == "__main__":
    main()
