"""Legacy .sng imports (GTS!/GTS2/GTS3/GTS4) against GoatTracker 2.76.

Each pinned example song is imported with ``convert=True`` and rewritten;
the result must be byte-identical to GoatTracker 2.76's own
``loadsong()`` + ``savesong()`` output (hashes recorded from ``gt2reloc.c``
of the 2.76 release), so no GoatTracker build is needed here.
"""

from hashlib import sha256

import pytest

from pygoattracker.reader import read_sng
from pygoattracker.writer import build_sng

from tests._fixture_cache import _archive_member

_ALIAS = {
    "V206": "goattrk2_206.zip",
    "V235": "goattrk2_235.zip",
    "V261": "goattrk2_261.zip",
}

# (archive, member, sha256 of the member, sha256 of GoatTracker 2.76's load/save)
LEGACY_SONGS = [
    (
        _ALIAS["V206"],
        "examples/2xtest.sng",
        "66a6c62b6c3ac386e631f00f7559a5665932890204d1b64440fd884c3066c563",
        "04f45d1230882295a1bbc6702ee3406b5d20f76e2da8c12aa199793218aa8c81",
    ),
    (
        _ALIAS["V206"],
        "examples/consultant.sng",
        "f5ddceeaa54284a0c4cdfe3ccfacf4fc4a78d8a63824ca4987b8a08e143f4f7c",
        "8a4e114ee1fe9b132ae939dd6435a1fb980bec82ba20680721e33158d5f71fbe",
    ),
    (
        _ALIAS["V206"],
        "examples/dojo.sng",
        "0dc659f11a7fbf1c0199d42e4b6827a3cf9bd67b6bc1f8930ecdace3aea330c5",
        "ea2d0be5ed1f86e9834ac1c5e297ce8c25940ad5f1d3c7d4793248bac4ec69f7",
    ),
    (
        _ALIAS["V206"],
        "examples/funktest.sng",
        "e16accf597ea8aaaa7a3f2b989a0830c9c0b1b4d51243fe135c219854e52ea53",
        "6fc1b6eba487a88eff6316a40f3ea87e44abfecc6a44f6b4428f2f78c4b61a89",
    ),
    (
        _ALIAS["V206"],
        "examples/phobos.sng",
        "c7ccd5ec4848896460816cac24c98dd13cae66c0058160fbdda911fe567e7eae",
        "a4b7800c9ed44573e11724766641025a3fc5ebd1890a3b0d68f8d922be85fff3",
    ),
    (
        _ALIAS["V206"],
        "examples/unleash.sng",
        "26f23edea26d73fae28341a030aee0ea12fe0c04504c758a2a5c69cc9261f49c",
        "e0eadb7847d8535ca9a6d6705e11cd01d5b0d7ab2e70f2cafe53134acac2435d",
    ),
    (
        _ALIAS["V235"],
        "examples/2xtest.sng",
        "0f8f02cc83bf8adaede9ac7514426a3825cfd484c83220a191a604db74ed09bc",
        "d36bf09acec5f4d2644a211e694bc51abc66f80e3a23fbd76fa984ac988a8afb",
    ),
    (
        _ALIAS["V235"],
        "examples/cabrinigreen.sng",
        "63e63566124ed45cbd33afea371efa02159d39e2d4c07e62d314161c7cac5712",
        "b43e2ee555b17f6397329027fb2091c6730de38825ea47bc420752fbaae90762",
    ),
    (
        _ALIAS["V235"],
        "examples/consultant.sng",
        "6765ccd4a6ad712ce69a476b1ef19d4d3792ab7afd25eada244eadf684c64ead",
        "8a4e114ee1fe9b132ae939dd6435a1fb980bec82ba20680721e33158d5f71fbe",
    ),
    (
        _ALIAS["V235"],
        "examples/dojo.sng",
        "ac3086bc02d63d20af475d1f3a89ac2585afd1dbe984e43c2f7d626ad31fbab7",
        "ea2d0be5ed1f86e9834ac1c5e297ce8c25940ad5f1d3c7d4793248bac4ec69f7",
    ),
    (
        _ALIAS["V235"],
        "examples/everlasting.sng",
        "a38a5a5619d3b27f9e3900823066677e1f0d664ca6abbb29f917611df0a209fe",
        "1b53d70de1864e85c25c6f4128185b3312c95a4dc109c7af7b326d4e9fb45074",
    ),
    (
        _ALIAS["V235"],
        "examples/funktest.sng",
        "474c8760c4754bab12998e96c7687eaebba6adb37954893e6e99c923925c3345",
        "6fc1b6eba487a88eff6316a40f3ea87e44abfecc6a44f6b4428f2f78c4b61a89",
    ),
    (
        _ALIAS["V235"],
        "examples/ghosttrackers.sng",
        "2e802a4c68c0c4fed7b7780f9ce1b29eefd984f5280bfc953d781a05c46d239c",
        "ff9291b33915460428405d72c5f0ad7d14e77572cd5b23ea5da56785f563f28b",
    ),
    (
        _ALIAS["V235"],
        "examples/hyperspace.sng",
        "157ab8b3f5e9b8a5ada697b30f07de05e8e23f6024ae0486ca380bf309dcf55c",
        "1e56da67ede8f6b9f64b8bfa986cfaddd9242346ad7bcdaf2cbf1059c553fe1f",
    ),
    (
        _ALIAS["V235"],
        "examples/phobos.sng",
        "7df88ef6673a63eb63a0da159c8b85d1c4ea9938bfde452efcb1e6def6b3aed8",
        "eeee6e607a575de525aa60f5f970c8baca81d375d6ccd637bd00b4aece7c4bc8",
    ),
    (
        _ALIAS["V235"],
        "examples/sanction.sng",
        "8311f277c4de80b8f6cb7d7091fe1f368aaeed769a402f5f2dff79598a135b44",
        "dbd105aa359ab0eba5735d42972a3b28efa0e52241b411c476651b01724423d2",
    ),
    (
        _ALIAS["V235"],
        "examples/sixpack.sng",
        "d9571273e317186164e0ed2afa7fe7d55a789f170fabb5e78b242e01ffe505b8",
        "1a6d9c0cbe79d340ee19e8a8ecb302f4636c2b610594557e3fc067c983547b87",
    ),
    (
        _ALIAS["V235"],
        "examples/stargate.sng",
        "dd94b5372dbae3de0cf0e70060836657090747fcaa8c444a29e913b43bb43245",
        "74b39014b80d082313ca7680d9131778a5b4993bc4514ab0c4d5b49dc889b7c0",
    ),
    (
        _ALIAS["V235"],
        "examples/tempo2test.sng",
        "b8dd7087817d72d4adb2f7162d26876ad15b61a8fedd2148948a254ca0772ee6",
        "30619544a67521df6c982f5eedd9b3846cc18b92f04969078d46438fa85bcb8d",
    ),
    (
        _ALIAS["V235"],
        "examples/transylvanian.sng",
        "b9166b412ad45318d111fd5277609405af3224d1a19157b066205321bf88a25d",
        "8d85d75c9545b35a7e68b4f23f6339fb75bb3aae590216e7bf0253fc36132529",
    ),
    (
        _ALIAS["V235"],
        "examples/unleash.sng",
        "09e2881981eda7ecbaac67725c0fc059f985779f39565541b92c6666c64a797e",
        "e0eadb7847d8535ca9a6d6705e11cd01d5b0d7ab2e70f2cafe53134acac2435d",
    ),
    (
        _ALIAS["V261"],
        "examples/2xtest.sng",
        "81081d0ecd930e3a38c5a4b05d328527eba07aa9316fe47fe194991fae961b42",
        "d36bf09acec5f4d2644a211e694bc51abc66f80e3a23fbd76fa984ac988a8afb",
    ),
    (
        _ALIAS["V261"],
        "examples/cabrinigreen.sng",
        "2d78ad20358e853be12359c38b6b91dbbe56c44d74e96477021e28612978379d",
        "b43e2ee555b17f6397329027fb2091c6730de38825ea47bc420752fbaae90762",
    ),
    (
        _ALIAS["V261"],
        "examples/consultant.sng",
        "fb3d8550b87dbfaf7c036d91f67f299d8c675ef80efdce2aaae0a2717f2ae9dd",
        "8a4e114ee1fe9b132ae939dd6435a1fb980bec82ba20680721e33158d5f71fbe",
    ),
    (
        _ALIAS["V261"],
        "examples/dojo.sng",
        "1016d82b042553d0e32235837946b156cd6f3103027586726765632d5bb72df0",
        "ea2d0be5ed1f86e9834ac1c5e297ce8c25940ad5f1d3c7d4793248bac4ec69f7",
    ),
    (
        _ALIAS["V261"],
        "examples/everlasting.sng",
        "1a8fbe2bb576ca714719f61bc4f1906b6c2e128257e0411216cfe2c430408e96",
        "1b53d70de1864e85c25c6f4128185b3312c95a4dc109c7af7b326d4e9fb45074",
    ),
    (
        _ALIAS["V261"],
        "examples/funktest.sng",
        "7922b8db27bd58bb6bac3a6f8e4d9f22060ff49e97beaafe5c5d56a31bdb4a48",
        "6fc1b6eba487a88eff6316a40f3ea87e44abfecc6a44f6b4428f2f78c4b61a89",
    ),
    (
        _ALIAS["V261"],
        "examples/ghosttrackers.sng",
        "6d72716728b84926a164f70db941c600275237f54600434eb6b9ce2d83cc9c98",
        "ff9291b33915460428405d72c5f0ad7d14e77572cd5b23ea5da56785f563f28b",
    ),
    (
        _ALIAS["V261"],
        "examples/hyperspace.sng",
        "36b60f78f73b9dd6ac3ecf9f3ed534b24303c92d65fdb810792cc0247db48ba6",
        "1e56da67ede8f6b9f64b8bfa986cfaddd9242346ad7bcdaf2cbf1059c553fe1f",
    ),
    (
        _ALIAS["V261"],
        "examples/sanction.sng",
        "13ac6766c6f6aff2c370f8d135533800f0824e99c0ed1b2868772a1562fd4240",
        "dbd105aa359ab0eba5735d42972a3b28efa0e52241b411c476651b01724423d2",
    ),
    (
        _ALIAS["V261"],
        "examples/sixpack.sng",
        "ad8907bfa227670517fbfd72708565f62b1be1e7e107789083df97f712032838",
        "1a6d9c0cbe79d340ee19e8a8ecb302f4636c2b610594557e3fc067c983547b87",
    ),
    (
        _ALIAS["V261"],
        "examples/tempo2test.sng",
        "72e026fb8592c1608a21ea202d66b47445cd8da4981e6a49b7fd678136e2719c",
        "30619544a67521df6c982f5eedd9b3846cc18b92f04969078d46438fa85bcb8d",
    ),
    (
        _ALIAS["V261"],
        "examples/transylvanian.sng",
        "19a6d9b1cff0f504f54913374d8db914f56b6dc5a08ba570ac7b7d6d85b597f4",
        "8d85d75c9545b35a7e68b4f23f6339fb75bb3aae590216e7bf0253fc36132529",
    ),
    (
        _ALIAS["V261"],
        "examples/unleash.sng",
        "7b02d4b136c1ec04c733ec76819e202581a01365c5f7387cfbc8a5e9cbb6d486",
        "e0eadb7847d8535ca9a6d6705e11cd01d5b0d7ab2e70f2cafe53134acac2435d",
    ),
    (
        _ALIAS["V261"],
        "examples/wavecmdtest.sng",
        "d738b10da4813f6e909f22e2019c89715c7f78ed6ceb43b540aec23fba867adc",
        "29139e1be51a75febc6a61b22767e5689f5009667a93acd050270a34b61fbcc1",
    ),
    (
        "goattrk.zip",
        "examples/2x_speed.sng",
        "6f0b53890d34af40feb582c1965077e0aa62626105a28b19bae7c33b4a42f501",
        "808030a5d0827edc9fbd3f0620f2a0e0c0c2d8072205460be615399e4cd8ba14",
    ),
    (
        "goattrk.zip",
        "examples/dojo.sng",
        "cee3451227d8cda831033af98d8c2dd4b73900a7c937c59bf3ee17571fc586bd",
        "fd274d9e131542896365705eacbfe2d8f641645911cc389c8db8382555df8bd7",
    ),
    (
        "goattrk.zip",
        "examples/funktest.sng",
        "76424e6ac976530e50327a87fda685c0c7573fe1089794a8e179b8e4280624bc",
        "f06745a084aab717fd3f614c8bbd86eab7c02f9579b835702b618190a1737197",
    ),
    (
        "goattrk.zip",
        "examples/loader.sng",
        "30d62f3c3752ddbaa658c33c68b4f424029b5b5656ae029bb7ce36eba8cc0757",
        "e399a219f40bace464107ae5e852ee8ca914f897d7ed356afda15d07238b077f",
    ),
    (
        "goattrk.zip",
        "examples/mwremix.sng",
        "1f84d1673cbc7b4cb7e4b1e05f071120904bb282a057cc43f64a958e9790c56a",
        "69481848e67c94fdac3e41fe27a2216f64c815e3d8118209a9d3a832cde2130c",
    ),
    (
        "goattrk.zip",
        "examples/nintendo.sng",
        "e652f7b206a74087500e4097f327b9d7d254389c3a0d15845e1dab7f21bb1302",
        "e4d9436fe1bec7dfef2a0031529ddf85337b0a8d11a5e995520a1a9ada3103e2",
    ),
    (
        "goattracker_v1.4b.zip",
        "EXAMPLES/MW4DOJO.SNG",
        "aa393ede82c654991e2bfbe2bf3d9b39953d2615865d33cf7f625f57ad380830",
        "6edea502d21913b773ad92cde815c77009641dd848379fff36d4ddf7140bd987",
    ),
    (
        "goattracker_v1.4b.zip",
        "EXAMPLES/MW4LOAD.SNG",
        "43bb9594b91e8cc30fa9141dbf8c324b6f74feb176f5711f72d0fb2b8e2b070f",
        "f1db3f77da010ea6739f43d40487ebeba1e1c07b4ec39fec6ce12c0049295bf3",
    ),
    (
        "goattracker_v1.4b.zip",
        "EXAMPLES/MWREMIX.SNG",
        "437729a524155953035cc7432b3c157b7429b15d34d3af0533ed03b937c7d2d5",
        "632ec0006e722767d00f60e2bf1253a76cef9c69ccf0185f6a756b34f55a1b09",
    ),
    (
        "goattracker_v1.4b.zip",
        "EXAMPLES/MWREMIX2.SNG",
        "8413decb856c1cbbbb8a8562f2702fc57f5a6302f7685f1b2d833491a36131fe",
        "cc6189d6d4713d9cd7f9d33a7bd0399849e66d806d146895d66935872c4bfdd2",
    ),
    (
        "goattracker_v1.4b.zip",
        "EXAMPLES/NINTENDO.SNG",
        "e652f7b206a74087500e4097f327b9d7d254389c3a0d15845e1dab7f21bb1302",
        "e4d9436fe1bec7dfef2a0031529ddf85337b0a8d11a5e995520a1a9ada3103e2",
    ),
]


@pytest.mark.parametrize(
    "archive,member,sha_in,sha_out",
    LEGACY_SONGS,
    ids=[
        f"{a.removesuffix('.zip')}-{m.rsplit('/', 1)[-1]}"
        for a, m, _, _ in LEGACY_SONGS
    ],
)
def test_import_matches_goattracker_276(archive, member, sha_in, sha_out):
    data = _archive_member(archive, member, sha_in)
    rebuilt = build_sng(read_sng(data, convert=True))
    assert sha256(rebuilt).hexdigest() == sha_out
