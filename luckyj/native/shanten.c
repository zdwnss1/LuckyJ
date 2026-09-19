/* Four-player regular shanten, adapted from MahjongRepository/mahjong
 * d16e68f378a7ff4457073f9eec52c5f8d965e03c, MIT. See licenses/mahjong.txt.
 * Copyright (c) 2017 mahjong Python library contributors.
 * Optional accelerator; the Python implementation remains the fallback. */
#include <stdint.h>
typedef struct {int h[34],m,t,p,j,best; uint32_t four,iso;} State;
static void run(State *s,int k);
static void branch(State*s,int a,int b,int c,int *field,int depth) {
    s->h[a]--; if(b>=0)s->h[b]--; if(c>=0)s->h[c]--; (*field)++;
    run(s,depth);
    (*field)--;s->h[a]++; if(b>=0)s->h[b]++; if(c>=0)s->h[c]++;
}
static void isolated(State*s,int k) {
    s->h[k]--;s->iso |= 1u<<k;run(s,k+1);s->h[k]++;s->iso &= ~(1u<<k);
}
static void run(State*s,int k) {
    if(s->best==-1)return;
    while(k<27&&!s->h[k])k++;
    if(k==27) {
        int value=8-2*s->m-s->t-s->p, candidates=s->m+s->t;
        if(s->p)candidates+=s->p-1;
        else if(s->four&&s->iso&&(s->four|s->iso)==s->four)value++;
        if(candidates>4)value+=candidates-4;
        if(value!=-1&&value<s->j)value=s->j;
        if(value<s->best)s->best=value;
        return;
    }
    int n=k%9;
    if(s->h[k]==4) {
        s->h[k]-=3;s->m++;
        if(n<7&&s->h[k+2]) {
            if(s->h[k+1])branch(s,k,k+1,k+2,&s->m,k+1);
            branch(s,k,k+2,-1,&s->t,k+1);
        }
        if(n<8&&s->h[k+1])branch(s,k,k+1,-1,&s->t,k+1);
        isolated(s,k);s->h[k]+=3;s->m--;
        s->h[k]-=2;s->p++;
        if(n<7&&s->h[k+2]) {
            if(s->h[k+1])branch(s,k,k+1,k+2,&s->m,k);
            branch(s,k,k+2,-1,&s->t,k+1);
        }
        if(n<8&&s->h[k+1])branch(s,k,k+1,-1,&s->t,k+1);
        s->h[k]+=2;s->p--;
    }
    if(s->h[k]==3) {
        branch(s,k,k,k,&s->m,k+1);s->h[k]-=2;s->p++;
        if(n<7&&s->h[k+1]&&s->h[k+2])branch(s,k,k+1,k+2,&s->m,k+1);
        else {
            if(n<7&&s->h[k+2])branch(s,k,k+2,-1,&s->t,k+1);
            if(n<8&&s->h[k+1])branch(s,k,k+1,-1,&s->t,k+1);
        }
        s->h[k]+=2;s->p--;
        if(n<7&&s->h[k+1]>=2&&s->h[k+2]>=2) {
            s->h[k]-=2;s->h[k+1]-=2;s->h[k+2]-=2;s->m+=2;
            run(s,k);
            s->m-=2;s->h[k]+=2;s->h[k+1]+=2;s->h[k+2]+=2;
        }
    }
    if(s->h[k]==2) {
        branch(s,k,k,-1,&s->p,k+1);
        if(n<7&&s->h[k+1]&&s->h[k+2])branch(s,k,k+1,k+2,&s->m,k);
    }
    if(s->h[k]==1) {
        if(n<6&&s->h[k+1]==1&&s->h[k+2]&&s->h[k+3]!=4)branch(s,k,k+1,k+2,&s->m,k+2);
        else {
            isolated(s,k);
            if(n<7&&s->h[k+2]) {
                if(s->h[k+1])branch(s,k,k+1,k+2,&s->m,k+1);
                branch(s,k,k+2,-1,&s->t,k+1);
            }
            if(n<8&&s->h[k+1])branch(s,k,k+1,-1,&s->t,k+1);
        }
    }
}
int luckyj_regular(const unsigned char *h) {
    State s={0};s.best=8;int n=0;uint32_t f=0,isol=0;
    for(int i=0;i<34;i++){s.h[i]=h[i];n+=h[i];if(h[i]>4)return -999;}
    if(n>14||n%3==0)return -999;
    for(int i=27;i<34;i++) {
        if(h[i]==4){s.m++;s.j++;f|=1u<<(i-27);isol|=1u<<(i-27);}
        else if(h[i]==3)s.m++;
        else if(h[i]==2)s.p++;
        else if(h[i]==1)isol|=1u<<(i-27);
    }
    if(s.j&&n%3==2)s.j--;
    if(isol){s.iso|=1u<<27;if((f|isol)==f)s.four|=1u<<27;}
    for(int i=0;i<27;i++)if(h[i]==4)s.four|=1u<<i;
    s.m+=(14-n)/3;run(&s,0);return s.best;
}
