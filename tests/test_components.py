import sys, tempfile, unittest, os
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controllers'/'PythonEquivalent'))
from RoverBlockchain import Blockchain, Block
from RAFTConsensus import RAFTConsensus

class Emitter:
    def __init__(self): self.messages=[]
    def send(self,msg): self.messages.append(msg)
class Receiver:
    def getQueueLength(self): return 0

class Components(unittest.TestCase):
    def test_genesis(self):
        self.assertEqual(Blockchain().chain[0].hash,Blockchain().chain[0].hash)
    def test_replication(self):
        a,b=Blockchain(),Blockchain()
        block=a.make_new_block({'event':'Discovery'},'moose',1,3.0)
        self.assertTrue(a.add_block(block))
        self.assertTrue(b.add_block(Block.from_dict(block.to_dict())))
        self.assertEqual(a.chain[-1].hash,b.chain[-1].hash)
        self.assertTrue(b.is_valid_chain())
    def test_tamper(self):
        a=Blockchain(); block=a.make_new_block({'x':1},'moose',1,3.0)
        block.data['x']=2
        self.assertFalse(a.add_block(block))
    def test_election_target_and_quorum(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd=os.getcwd(); os.chdir(tmp)
            try:
                r=RAFTConsensus(Blockchain(),'a',Emitter(),Receiver(),['a','b','c'],50,time_fn=lambda:0)
                r._start_election()
                r._on_vote({'term':1,'to':'c','from':'b','granted':True})
                self.assertEqual(r.state,'candidate')
                r._on_vote({'term':1,'to':'a','from':'b','granted':True})
                self.assertEqual(r.state,'leader')
            finally: os.chdir(cwd)
    def test_timeout_with_known_leader(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd=os.getcwd(); os.chdir(tmp)
            try:
                now=[0.0]; r=RAFTConsensus(Blockchain(),'a',Emitter(),Receiver(),['a','b','c'],50,time_fn=lambda:now[0])
                r._on_heartbeat({'term':0,'leader':'b'})
                now[0]=10.0; r.step()
                self.assertEqual(r.state,'candidate')
            finally: os.chdir(cwd)
if __name__=='__main__': unittest.main()
