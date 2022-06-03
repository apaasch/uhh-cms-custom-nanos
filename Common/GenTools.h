#pragma once
#include "AnalysisTools.h"


std::map<int, float> particleMasses {
  {11, 0.00051099894},
  {12, 0.},
  {13, 1.0565837},
  {14,0.},
  {15, 1.77686},
  {16, 0.},
  {22, 0.},
  {111, 0.134977},
  {-111, 0.134977},
  {211, 0.13957},
  {-211, 0.13957},
  {311, 0.497611},
  {-311, 0.497611},
  {321, 0.493677},
  {-321, 0.493677},
  {421, 1.86483},
  {-421, 1.86483},
  {411, 1.8695},
  {-11, 1.8695}
};


double muon_mass = particleMasses.at(13);
double electron_mass = particleMasses.at(11);


class ParticleDB {
public:
  static void Initialize(const std::string_view inputFile) {
    std::ifstream file (inputFile, ios::in );
     std::string line;
     std::string name, type;
     int pdg, charge;
     while (getline(file, line)){
       std::stringstream ss(line);
       //std::cout<<line<<std::endl;
       std::string name, type, pdgid ,charge;
       int pdgid_value;
       int charge_value;
       ParticleInfo currentInfo;
       std::getline(ss,pdgid,',');
       pdgid_value = stoi(pdgid);
       std::getline(ss,name,',');
       std::getline(ss,type,',');
       std::getline(ss,charge,',');
       charge_value = stoi(charge);

       currentInfo.pdgId = pdgid_value;
       currentInfo.name = name;
       currentInfo.type= type;
       currentInfo.charge= charge_value;
       particles().insert ( std::pair<int,ParticleInfo>(pdgid_value,currentInfo) );
     }

  }

  static const ParticleInfo& GetParticleInfo(int pdgId)
  {
    if(particles().empty())
      throw std::runtime_error("ParticleDB is not initialized.");
    auto iter = particles().find(pdgId);
    if(iter == particles().end()){
      std::cout<<pdgId<<std::endl;
      throw std::runtime_error("ParticleInfo not found for particle ID");}
    return iter->second;
  }
private:
  static std::map<int, ParticleInfo>& particles()
  {
    static std::map<int, ParticleInfo> p;
    return p;
  }

};



RVecI GetDaughters(const int& mother_idx, RVecI& already_considered_daughters, const RVecI& GenPart_genPartIdxMother ){
  RVecI daughters;
  //std::vector<std::vector<int>> daughters;
  for (int daughter_idx =mother_idx; daughter_idx<GenPart_genPartIdxMother.size(); daughter_idx++){

    if(GenPart_genPartIdxMother[daughter_idx] == mother_idx && !(std::find(already_considered_daughters.begin(), already_considered_daughters.end(), daughter_idx)!=already_considered_daughters.end())){
      daughters.push_back(daughter_idx);
      already_considered_daughters.push_back(daughter_idx);
    }
  }
  return daughters;

}
RVecI GetMothers(const int &part_idx, const RVecI& GenPart_genPartIdxMother ){
  RVecI mothers;
  int new_idx = part_idx;
  int mother_idx = GenPart_genPartIdxMother[new_idx];
  while(mother_idx >=0 ){
    mothers.push_back(mother_idx);
    new_idx = mother_idx;
    mother_idx = GenPart_genPartIdxMother[new_idx];

  }
  return mothers;

}

RVecI GetLastHadrons(const RVecI& GenPart_pdgId, const RVecI& GenPart_genPartIdxMother){

  RVecI already_considered_daughters;
  RVecI lastHadrons;
  //if(evt!=905) return lastHadrons;

  for(int part_idx =0; part_idx<GenPart_pdgId.size(); part_idx++){
    RVecI daughters = GetDaughters(part_idx, already_considered_daughters, GenPart_genPartIdxMother);
    RVecI mothers = GetMothers(part_idx, GenPart_genPartIdxMother);

    bool comesFrom_b = false;
    bool comesFrom_H = false;
    bool hasHadronsDaughters = false;

    for (auto& mother : mothers){
      if(abs(GenPart_pdgId[mother])==5) comesFrom_b=true;
      if(abs(GenPart_pdgId[mother])==25) comesFrom_H=true;
    }
    for (auto& daughter:daughters){
      ParticleInfo daughterInformation = ParticleDB::GetParticleInfo(GenPart_pdgId[daughter]);
      if(daughterInformation.type == "baryon" || daughterInformation.type == "meson"){
        hasHadronsDaughters = true;
      }
    }
    if(comesFrom_b && comesFrom_H && !hasHadronsDaughters){
      ParticleInfo lastHadronInformation = ParticleDB::GetParticleInfo(GenPart_pdgId[part_idx]);
      if(lastHadronInformation.type == "baryon" || lastHadronInformation.type == "meson"){
        lastHadrons.push_back(part_idx);
      }
    }
  }
  return lastHadrons;
}



void PrintDecayChainParticle(const ULong64_t evt, const int& mother_idx, const RVecI& GenPart_pdgId, const RVecI& GenPart_genPartIdxMother, const RVecI& GenPart_statusFlags, const RVecF& GenPart_pt, const RVecF& GenPart_eta, const RVecF& GenPart_phi, const RVecF& GenPart_mass, const RVecI& GenPart_status, const std::string pre, RVecI &already_considered_daughters, std::ostream& os)
{
  ParticleInfo particle_information = ParticleDB::GetParticleInfo(GenPart_pdgId[mother_idx]);
  RVecI daughters = GetDaughters(mother_idx, already_considered_daughters, GenPart_genPartIdxMother);
  const float particleMass = (particleMasses.find(GenPart_pdgId[mother_idx]) != particleMasses.end()) ? particleMasses.at(GenPart_pdgId[mother_idx]) : GenPart_mass[mother_idx];
  const LorentzVectorM genParticle_momentum = LorentzVectorM(GenPart_pt[mother_idx], GenPart_eta[mother_idx], GenPart_phi[mother_idx],particleMass);
  int mother_mother_index = GenPart_genPartIdxMother[mother_idx];
  const auto flag = GenPart_statusFlags[mother_idx];

  os << particle_information.name      << " <" << GenPart_pdgId[mother_idx]
     << "> pt = " << genParticle_momentum.Pt()      << " eta = " << genParticle_momentum.Eta()
     << " phi = " << genParticle_momentum.Phi()     << " E = " << genParticle_momentum.E()
     << " m = "   << genParticle_momentum.M()       << " index = " << mother_idx
     << " flag = " << GetBinaryString(flag)     << " particleStatus = " << GenPart_status[mother_idx]
     << " charge = " << particle_information.charge << " type = " << particle_information.type
     << " mother_idx = " << mother_mother_index;
  os << "\n";

    if(daughters.size()==0 ) return;
    for(int d_idx =0; d_idx<daughters.size(); d_idx++) {
      int n = daughters[d_idx];
      os << pre << "+-> ";
      const char pre_first = d_idx == daughters.size() -1 ?  ' ' : '|';
      const std::string pre_d = pre + pre_first ;//+ "  ";
      PrintDecayChainParticle(evt, n, GenPart_pdgId, GenPart_genPartIdxMother, GenPart_statusFlags, GenPart_pt, GenPart_eta, GenPart_phi, GenPart_mass, GenPart_status, pre_d, already_considered_daughters, os);

  }
}
int PrintDecayChain(const ULong64_t evt, const RVecI& GenPart_pdgId, const RVecI& GenPart_genPartIdxMother, const RVecI& GenPart_statusFlags, const RVecF& GenPart_pt, const RVecF& GenPart_eta, const RVecF& GenPart_phi, const RVecF& GenPart_mass, const RVecI& GenPart_status,const std::string& outFile)
{
    std::ofstream out_file(outFile);
    RVecI already_considered_daughters;
    for(int mother_idx =0; mother_idx<GenPart_pdgId.size(); mother_idx++){
      bool isStartingParticle = ( GenPart_genPartIdxMother[mother_idx] == -1);//&& (GenPart_pdgId[mother_idx] == 21 || GenPart_pdgId[mother_idx] == 9);
      if(!isStartingParticle) continue;
      PrintDecayChainParticle(evt, mother_idx, GenPart_pdgId, GenPart_genPartIdxMother, GenPart_statusFlags, GenPart_pt, GenPart_eta, GenPart_phi, GenPart_mass, GenPart_status, "", already_considered_daughters, out_file);
    }

return 0;
}
