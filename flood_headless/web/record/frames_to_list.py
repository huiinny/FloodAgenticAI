import json,sys,os
d=sys.argv[1];idx=json.load(open(d+'/idx.json'))
with open(d+'/list.txt','w') as f:
    for (n,t),(_,t2) in zip(idx,idx[1:]+[[None,idx[-1][1]+2.0]]):
        f.write(f"file '{os.path.join(d,n)}'\nduration {max(t2-t,0.001):.4f}\n")
    f.write(f"file '{os.path.join(d,idx[-1][0])}'\n")
print(len(idx),'frames',round(idx[-1][1]-idx[0][1]),'s')
