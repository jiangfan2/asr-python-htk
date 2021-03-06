from __future__ import print_function

import glob
from multiprocessing.pool import Pool
import os
import re
import shutil

from gridscripts.remote_run import System
from htk2.tools import HCompV,HERest,HHEd,HLEd,HVite, Copier
from htk2.units import HTK_dictionary,HTK_transcription
import htk_file_strings


__author__ = 'peter'

class ExistingFilesException(Exception): pass

#class TrainLogger(object):
#    a = []
#    def __init__(self,f):
#        self.f = f
#        self.__name__ = f.__name__
#
#    def __call__(self,*args,**kwargs):
#        print("Calling %s" % self.__name__)
#        self.a.append(self.__name__)
#        self.f(self, *args,**kwargs)


class HTK_model(object):
    def __init__(self, name, model_dir, htk_config):
        self.name = name

        self.model_dir = model_dir
        self.train_files_dir = os.path.join(self.model_dir, self.name)

        self.training_scp = os.path.join(self.train_files_dir, 'train.scp')
        self.training_word_mlf = os.path.join(self.train_files_dir, 'word.mlf')
        self.training_phone_mlf = os.path.join(self.train_files_dir, 'phone.mlf')
        self.training_dict = os.path.join(self.train_files_dir,'dict')

        self.htk_config = htk_config
        self.training_files_speaker_name_chars = 3

        self.phones = []
        self.id = None


    def _get_model_name_id(self,prev=0,id=None):
        if id is None:
            id = self.id-prev
        return self.model_dir + '/' + "{0}.{1:02d}".format(self.name,id)
    

    def initialize_new(self, scp_list, word_mlf, dict, remove_previous=False):
        System.set_log_dir(self.name)
        if remove_previous:
            for f in glob.iglob(System.get_log_dir()+'/*'): os.remove(f)

        if not remove_previous and (os.path.exists(self.train_files_dir) or len(glob.glob(self.model_dir + '/' + self.name + '.*')) > 0):
            raise ExistingFilesException

        if os.path.exists(self.train_files_dir): shutil.rmtree(self.train_files_dir)
        for f in glob.iglob(self.model_dir + '/' + self.name + '.*'): os.remove(f)
        os.mkdir(self.train_files_dir)

        # handle dictionary
        dic = HTK_dictionary()
        if isinstance(dict,basestring):
            dic.read_dict(dict)
        elif all(isinstance(d,basestring) for d in dict):
            for d in dict:
                dic.read_dict(d)
        else:
            raise TypeError
        dic.write_dict(self.training_dict)

        self.phones = dic.get_phones()


        # handle transcription
        trans = HTK_transcription()
#        if isinstance(word_mlf,basestring):
#            trans.read_mlf(word_mlf, HTK_transcription.WORD)
#        elif all(isinstance(w,basestring) for w in word_mlf):
#            for w in word_mlf:
#                trans.read_mlf(w, HTK_transcription.WORD)
#        else:
#            raise TypeError
        word_mlf = word_mlf.strip().split(',')
        for w in word_mlf:
            trans.read_mlf(w,HTK_transcription.WORD)


        self.id = 1

        phones_list = self._get_model_name_id() + '.hmmlist'
        with open(phones_list, 'w') as phones_desc:
            for p in self.phones:
                print(p, file=phones_desc)


        # handle scp files
        scp_list = scp_list.strip().split(',')
#        if isinstance(scp_list,basestring):
#            scp_list = [scp_list]

        real_trans = HTK_transcription()
        real_trans.transcriptions[real_trans.WORD] = {}

        with open(self.training_scp, 'w') as scp_desc:
            for scp in scp_list:
                for file in open(scp):
                    id = os.path.splitext(os.path.basename(file.strip()))[0]
                    if not file.startswith('/'):
                        file = os.path.join(os.path.dirname(scp),file.strip())

                    ok = True

                    for word in trans.transcriptions[HTK_transcription.WORD][id]:
                        if not dic.word_in_dict(word):
                            print("%s skipped, because has missing word %s" % (file.strip(), word))
                            ok = False
                            break        
                    if ok:
                        print(file.strip(),file=scp_desc)
                        real_trans.transcriptions[real_trans.WORD][id] = trans.transcriptions[real_trans.WORD][id]

        real_trans.write_mlf(self.training_word_mlf,target=HTK_transcription.WORD)
        self.expand_word_transcription()

    def transfer_files_local(self):
        if not hasattr(self,'training_scp_orig'):
            self.training_scp_orig = self.training_scp
            tmp_dir = System.get_local_temp_dir()

            self.training_scp = os.path.join(tmp_dir,'training_scp_local.scp')

            files = []
            with open(self.training_scp, 'w') as scp_desc:

                for file in open(self.training_scp_orig):
                    file = file.strip()
                    files.append(file)
                    print(os.path.join(tmp_dir,os.path.basename(file)),file=scp_desc)

            pool = Pool()
            pool.map(Copier(tmp_dir),files)
            pool.close()
            pool.join()


    def clean_files_local(self):
        if hasattr(self,'training_scp_orig'):
            line = open(self.training_scp).readline().strip()
            shutil.rmtree(os.path.dirname(line))

            self.training_scp = self.training_scp_orig
            delattr(self,'training_scp_orig')

    def initialize_existing(self):
        pass

    def expand_word_transcription(self, use_sp=False):
        tmp_dir = System.get_global_temp_dir()
        mkmono = os.path.join(tmp_dir,'mkmono.led')

        with open(mkmono, 'w') as mkmono_desc:
            print(htk_file_strings.MKMONO ,file=mkmono_desc)
        
            if use_sp:
                self.training_phone_mlf = os.path.join(self.train_files_dir, 'phone1.mlf')
            else:
                self.training_phone_mlf = os.path.join(self.train_files_dir, 'phone0.mlf')
                print("DE sp", file=mkmono_desc)

        HLEd(self.htk_config, self.training_word_mlf, mkmono, self._get_model_name_id() + '.hmmlist', self.training_dict,self.training_phone_mlf).run()

        shutil.rmtree(tmp_dir)

    def align_transcription(self):
        i = 1
        while os.path.exists(os.path.join(self.train_files_dir, 'phone_aligned_{0:d}.mlf'.format(i))):
            i += 1

        tmp_dir = System.get_global_temp_dir()
        tmp_config = os.path.join(tmp_dir,'hvite_config')
        with open(tmp_config,'w') as tmp_desc:
            print(htk_file_strings.HVITE_CONFIG, file=tmp_desc)
        self.training_phone_mlf = os.path.join(self.train_files_dir, 'phone_aligned_{0:d}.mlf'.format(i))
        HVite(self.htk_config, self.training_scp, self._get_model_name_id() + '.mmf', self.training_dict, self._get_model_name_id() + '.hmmlist', self.training_phone_mlf, self.training_word_mlf,config_file=tmp_config).run()
        shutil.rmtree(tmp_dir)


    def flat_start(self):
        tmp_dir = System.get_global_temp_dir()
        proto_file = os.path.join(tmp_dir, 'proto')
        vFloors = os.path.join(tmp_dir, 'vFloors')

        with open(proto_file, 'w') as proto_desc:
            print(htk_file_strings.PROTO, file=proto_desc)

        HCompV(self.htk_config, self.training_scp, proto_file).run()


        out_model = self._get_model_name_id() + ".mmf"

        model_def = ""
        phone_def = ""
        in_model_def = True

        for line in open(proto_file):
            if line[0:2] == '~h': in_model_def = False
            if in_model_def: model_def += line
            else: phone_def += line

        #Write model file
        with open(out_model, 'w') as model_desc:
            print(model_def, file=model_desc)
            for line in open(vFloors):
                print(line.strip(), file=model_desc)

            #Write the hmmdefs  (replacing for each monophone, proto with the monophone)
            for line in open( self._get_model_name_id() + '.hmmlist'):
                print(phone_def.replace('proto', line.rstrip()), file=model_desc)
                
        shutil.rmtree(tmp_dir)
        
    def re_estimate(self,stats=False):
        self.id += 1
        if stats:
            stats = self._get_model_name_id()+'.stats'
        else:
            stats = None
        shutil.copyfile(self._get_model_name_id(1)+'.hmmlist',self._get_model_name_id()+'.hmmlist')
        HERest(self.htk_config, self.training_scp,self._get_model_name_id(1)+'.mmf',self._get_model_name_id()+'.hmmlist',
               self.training_phone_mlf,output_hmm_model=self._get_model_name_id()+'.mmf',stats=stats).run()


    def introduce_short_pause_model(self):
        self.id += 1
        phones = [p.strip() for p in open(self._get_model_name_id(1)+'.hmmlist')]
        phones.append('sp')

        with open(self._get_model_name_id()+'.hmmlist', 'w') as phone_out:
            for p in sorted(set(phones)):
                print(p,file=phone_out)

        #copy sil state 3 to sp
        in_sil = False
        in_state3 = False

        state = ""

        with open(self._get_model_name_id()+'.mmf', 'w') as model_desc:
            for line in open(self._get_model_name_id(1)+'.mmf'):
                print(line, file=model_desc)

                if line.startswith('~h'):
                    if line.startswith('~h "sil"'): in_sil = True
                    else: in_sil = False
                elif line.startswith('<STATE>'):
                    if line.startswith('<STATE> 3'): in_state3 = True
                    else: in_state3 = False
                elif in_sil and in_state3:
                    state += line

            print(  "~h \"sp\" <BEGINHMM> <NUMSTATES> 3", file=model_desc)
            print( "<STATE> 2", file=model_desc)
            print(  state, file=model_desc)
            print( htk_file_strings.TRANSP3, file=model_desc)

        self.id += 1
        shutil.copyfile(self._get_model_name_id(1)+'.hmmlist',self._get_model_name_id()+'.hmmlist')


        tmp_dir = System.get_global_temp_dir()
        with open(os.path.join(tmp_dir,'sil.hed'), 'w') as sil_desc:
            print( htk_file_strings.SIL_HED, file = sil_desc)
        HHEd(self.htk_config,self._get_model_name_id(1)+'.mmf', self._get_model_name_id()+'.mmf',self._get_model_name_id()+'.hmmlist',script=os.path.join(tmp_dir,'sil.hed')).run()
        shutil.rmtree(tmp_dir)

        self.expand_word_transcription(True)

    def transform_to_triphone(self):
        tmp_dir = System.get_global_temp_dir()
        mktri = os.path.join(tmp_dir,'mktri.led')

        with open(mktri, 'w') as mktri_desc:
            print(htk_file_strings.MKTRI ,file=mktri_desc)
        old_mlf = self.training_phone_mlf
        self.training_phone_mlf = os.path.join(self.train_files_dir, 'tri.mlf')
        self.id += 1
        HLEd(self.htk_config, old_mlf, mktri, self._get_model_name_id() + '.hmmlist', self.training_dict,self.training_phone_mlf).run()
        self._remove_triphone_sil(self._get_model_name_id() + '.hmmlist', True)
        self._remove_triphone_sil(self.training_phone_mlf)

        tri_hed = os.path.join(tmp_dir,'tri.hed')

        self._make_tri_hed(self._get_model_name_id() + '.hmmlist',self._get_model_name_id(1) + '.hmmlist',tri_hed)

        HHEd(self.htk_config,self._get_model_name_id(1)+'.mmf',self._get_model_name_id()+'.mmf',self._get_model_name_id(1)+'.hmmlist',script=tri_hed).run()
        
        shutil.rmtree(tmp_dir)    

    def tie_triphones(self):
        self.id += 1
        tmp_dir = System.get_global_temp_dir()
        full_list = os.path.join(tmp_dir,'full_list')

        self._make_full_list(self._get_model_name_id(5) + '.hmmlist',full_list)

        tree_hed = os.path.join(tmp_dir,'tree.hed')
        self._make_tree_hed(self.htk_config.tying_rules, self._get_model_name_id(5) + '.hmmlist',tree_hed,
                            self.htk_config.tying_threshold,self.htk_config.required_occupation,self._get_model_name_id(1) + '.stats',
                            full_list,self._get_model_name_id() + '.hmmlist', os.path.join(tmp_dir,'trees'))

        HHEd(self.htk_config,self._get_model_name_id(1) + '.mmf',self._get_model_name_id(0) + '.mmf',self._get_model_name_id(1) + '.hmmlist',script=tree_hed).run()

        shutil.rmtree(tmp_dir)

    def split_mixtures(self,num_mixes):
        self.id += 1
        tmp_dir = System.get_global_temp_dir()
        hed_file =  os.path.join(tmp_dir,'mix.hed')

        with open(hed_file, 'w') as hed:
            print("MU {0:d} {{*.state[2-4].stream[1].mix}}".format(num_mixes),file=hed)
            print("MU {0:d} {{sil.state[2-4].stream[1].mix}}".format(2 *num_mixes),file=hed)

        shutil.copyfile(self._get_model_name_id(1) + '.hmmlist',self._get_model_name_id() + '.hmmlist')
        HHEd(self.htk_config,self._get_model_name_id(1) + '.mmf',self._get_model_name_id(0) + '.mmf',self._get_model_name_id() + '.hmmlist',script=hed_file).run()
        shutil.rmtree(tmp_dir)

    def split_mixtures_variably(self,power, num_iterations):
        self.id += 1
        tmp_dir = System.get_global_temp_dir()
        hed_file =  os.path.join(tmp_dir,'mix.hed')

        with open(hed_file, 'w') as hed:
            print('LS "%s"' % (self._get_model_name_id(1) + '.stats'),file=hed)
            print("PS 16 %f %d" % (power, num_iterations),file=hed)

        shutil.copyfile(self._get_model_name_id(1) + '.hmmlist',self._get_model_name_id() + '.hmmlist')
        
        HHEd(self.htk_config,self._get_model_name_id(1) + '.mmf',self._get_model_name_id(0) + '.mmf',self._get_model_name_id() + '.hmmlist',script=hed_file).run()
        shutil.rmtree(tmp_dir)

#    def estimate_transform(self):
#        pass


    def clean_up(self,keep_versions = None):
        if keep_versions is None:
            keep_versions = set()
        else:
            keep_versions = set(keep_versions)

        for i in xrange(1,self.id):
            if i not in keep_versions:
                for file in glob.iglob(self._get_model_name_id(id=i)+'.*'):
                    os.remove(file)

    @staticmethod
    def _remove_triphone_sil(file, unique = False):
        lines = []
        reg = re.compile("([a-z_]+\-sil)|(sil\+[a-z_]+)")

        for line in open(file):
            lines.append(reg.sub('sil', reg.sub('sil', line.rstrip())))

        with open(file, 'w') as wfile:
            for line in lines:
                if not unique or (not line.rstrip() == 'sil' and not line.rstrip() == 'sil+sil'):
                    print(line, file=wfile)
            if unique:
                print("sil",file=wfile)

    @staticmethod
    def _make_tree_hed(phone_rules_file, phones_list, tree_hed_file, tb, ro, statsfile, fulllist, tiedlist, trees):

        phone_rules = {}
        for line in open(phone_rules_file):
            rule, phones = line.split(None, 1)
            if not phone_rules.has_key(rule):
                phone_rules[rule] = []
            phone_rules[rule].extend([phone.lower() for phone in phones.split()])

        for phone in open(phones_list):
            phone_rules[phone.rstrip()] = [phone.rstrip()]

        if phone_rules.has_key('sp'): del phone_rules['sp']
        if phone_rules.has_key('sil'): del phone_rules['sil']

        with open(tree_hed_file, 'w') as tree_hed:
            print("LS {0:>s}".format(statsfile),file=tree_hed)
            print("RO {0:.1f}".format(ro),file=tree_hed)
            print("TR 0",file=tree_hed)

            for rule, phones in phone_rules.items():
                print('QS "L_{0:>s}" {{{1:>s}}}'.format(rule, ",".join([phone + '-*' for phone in phones])),file=tree_hed)
                print('QS "R_{0:>s}" {{{1:>s}}}'.format(rule, ",".join(['*+' + phone  for phone in phones])),file=tree_hed)


            print("TR 2",file=tree_hed)

            for state in range(2,5):
                for phone in open(phones_list):
                    print(
                            'TB {tb:.1f} "{phone:>s}_s{state:d}" {{("{phone:>s}","*-{phone:>s}+*","{phone:>s}+*","*-{phone:>s}").state[{state:d}]}}'.format(
                                    **{'tb': tb, 'state': state, 'phone': phone.rstrip()}),file=tree_hed)

            print("TR 1",file=tree_hed)


            print('AU "{0:>s}"'.format(fulllist),file=tree_hed)
            print('CO "{0:>s}"'.format(tiedlist),file=tree_hed)
            print('ST "{0:>s}"'.format(trees),file=tree_hed)

    @staticmethod
    def _make_tri_hed(triphones_list, phones_list, tri_hed):
        with open(tri_hed, 'w') as trihed:
            print("CL %s" % triphones_list,file=trihed)
            for line in open(phones_list):
                print("TI T_%(phone)s {(*-%(phone)s+*,%(phone)s+*,*-%(phone)s).transP}" % {'phone': line.rstrip()}, file=trihed)


    @staticmethod
    def _make_full_list(phone_list, full_list):
        phones = []
        for phone in open(phone_list):
            if phone.rstrip() != 'sp': phones.append(phone.rstrip())

        with open(full_list, 'w') as flist:
            for phone1 in phones:
                for phone2 in phones:
                    if phone2 != 'sil':
                        for phone3 in phones:
                            print ("{0:>s}-{1:>s}+{2:>s}".format(phone1, phone2, phone3), file=flist)
            print ('sp', file=flist)
            print ('sil', file=flist)
