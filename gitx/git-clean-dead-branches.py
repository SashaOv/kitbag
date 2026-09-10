from plumbum.cmd import git

gone_branches = [
    line.split()[0] 
    for line in git["branch", "-vv"]().splitlines() 
    if ': gone]' in line
]

if gone_branches:
    git["branch", "-D"][gone_branches]()
    print(f"Deleted {len(gone_branches)} dead branches")

