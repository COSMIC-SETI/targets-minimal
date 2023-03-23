import redis
import pandas as pd
import yaml
import scipy.constants as constants
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
import numpy as np
import json
import time

#from sqlalchemy.orm import sessionmaker

import pymysql
pymysql.install_as_MySQLdb()

try:
    from .logger import log
except ImportError:
    from logger import log

class TargetsMinimal(object):
    """A minimal implementation of the target selector. It functions as follows:

       1. Subscribes to the `pointing_channel` - the Redis pub/sub channel to 
          which new pointings are to be published. These messages must be 
          formatted as follows:
          `<subarray name>:<target name>:<RA>:<Dec>:<FECENTER>:<OBSID>`
          RA and Dec should be in degrees, while `FECENTER` should be in MHz.

       2. When a new pointing message is received, the radius of the primary 
          beam is estimated using `FECENTER`. 

       3. Retrieves the list of targets available within the primary field of 
          view from the primary star database.

       4. Formats the list into a list of dictionaries:
          `[{source_id, ra, dec}, {source_id, ra, dec}, ... ]`
          Details of the primary pointing are included as follows:
          `{primary_pointing, primary_ra, primary_dec}`

       5. This list is JSON-formatted and saved in Redis under the key:
          `targets:<OBSID>`

       6. The key is published to the `targets_channel`. 
    """

    def __init__(self, redis_endpoint, pointing_channel, targets_channel, config_file):
        """Initialise the minimal target selector. 

        Args:
            redis_endpoint (str): Redis endpoint (of the form <host IP
            address>:<port>) 
            pointing_channel (str): Name of the channel from which the minimal
            target selector will receive new pointing information.  
            targets_channel (str): Name of the channel to which the minimal 
            target selector will publish target information.  
            config_file (str): Location of the database config file (yml).

        Returns:
            None
        """
        log.info('Initialising the minimal target selector, session commits true.')
        redis_host, redis_port = redis_endpoint.split(':')
        self.redis_server = redis.StrictRedis(host=redis_host, 
                                              port=redis_port, 
                                              decode_responses=True)
        self.pointing_channel = pointing_channel
        self.targets_channel = targets_channel
        self.configure_db(config_file)

    def start(self):
        """Start the minimal target selector.
        """
        log.info('Starting minmial target selector.')
        log.info('Listening for new pointings on Redis channel: {}'.format(self.pointing_channel))
        ps = self.redis_server.pubsub(ignore_subscribe_messages=True)
        ps.subscribe(self.pointing_channel)
        for msg in ps.listen():
            self.parse_msg(msg)

    def configure_db(self, config_file):
        """Configure access to the database of sources.

        Args:
            config_file (str): File path to the .yml DB configuration file. 

        Returns:
            None
        """
        cfg = self.read_config_file(config_file)
        url = URL(**cfg)
        self.engine = create_engine(url)
#        self.connection = self.engine.connect()
        
       # Session = sessionmaker(bind=self.engine)
       # self.session = Session()

    def read_config_file(self, config_file):
        """Read the database configuration yaml file.

        Args:
            config_file (str): File path to the .yml DB configuration file. 

        Returns:
            None
        """
        try:
            with open(config_file, 'r') as f:
                try:
                    cfg = yaml.safe_load(f)
                    return(cfg['mysql'])
                except yaml.YAMLError as E:
                    log.error(E)
        except IOError:
            log.error('Config file not found')
 
    def parse_msg(self, msg):
        """Examines and parses incoming messages, and initiates the
        appropriate response.

        Expects a message of the form:

        `<OBSID>:<target name>:<RA (deg)>:<Dec (deg)>:<FECENTER (MHz)>`

        Note that OBSID must be constructed as follows:

        `<telescope name>:<subarray name>:<PKTSTART timestamp>`
 
        Args:
            msg (str): The incoming message from the `pointing_channel`. 

        Returns:
            None
        """
        msg_data = msg['data']
        msg_components = msg_data.split(':')
        # Basic checks of incoming message
        if(len(msg_components) == 7):
            log.info('Processing message: {}'.format(msg_data))
            telescope_name = msg_components[0]       
            subarray = msg_components[1]       
            pktstart_ts = msg_components[2]       
            target_name = msg_components[3]       
            ra_deg = float(msg_components[4])       
            dec_deg = float(msg_components[5])
            fecenter = float(msg_components[6])
            obsid = '{}:{}:{}'.format(telescope_name, subarray, pktstart_ts)
            self.calculate_targets(subarray, target_name, ra_deg, dec_deg, fecenter, obsid)
        else:
            log.warning('Unrecognised message: {}'.format(msg_data))

    def query_bounds(self, ra, dec, r):
        """Calculate bounds of a rectangular box encompassing the current 
        field of view. 

        See also:
            https://github.com/UCBerkeleySETI/target-selector
            http://janmatuschek.de/LatitudeLongitudeBoundingCoordinates#PolesAnd180thMeridian

        Allows fast retrieval of a smaller subset of the full 
        target list, from which sources in the current field of view can be
        retrieved.  

        Assumes r < pi/2

        Args:        
            ra (float): RA in radians of the primary pointing target. 
            J2000 coordinates should be used if the original 26M Gaia 
            DR2-derived star list is used.
            dec (float): As above, Dec in radians. 
            r (float): Angular radius of field of view in radians. 

        Returns:
            bounds (list): Coordinates of corners of a bounding box
            encompassing the current field of view, in radians.
            Note: returns two bounding boxes if the bounding box 
            overlaps 0 deg RA. 
        """
        if((dec + r) >= np.pi/2):
           dec_max = np.pi/2
           dec_min = dec - r 
           ra_min = 0
           ra_max = 2*np.pi
           bounds = [[ra_min, ra_max, dec_min, dec_max]]
        elif((dec - r) <= -np.pi/2.0):
           dec_min = np.pi/2
           dec_max = dec + r 
           ra_min = 0
           ra_max = 2*np.pi
           bounds = [[ra_min, ra_max, dec_min, dec_max]]
        else:
           dec_min = dec - r
           dec_max = dec + r
           ra_off = np.arcsin(np.sin(r)/np.cos(dec))
           ra_min = ra - ra_off
           ra_max = ra + ra_off
           if(ra_min < 0):
               ra_min_0 = 2*np.pi - ra_min
               ra_max_0 = 0
               ra_min_1 = 0
               ra_max_1 = ra_max
               bounds = [[ra_min_0, ra_max_0, dec_min, dec_max], 
                         [ra_min_1, ra_max_1, dec_min, dec_max]]
           elif(ra_max > 2*np.pi):
               ra_min_0 = ra_min
               ra_max_0 = 2*np.pi
               ra_min_1 = 0
               ra_max_1 = ra_max - 2*np.pi
               bounds = [[ra_min_0, ra_max_0, dec_min, dec_max], 
                         [ra_min_1, ra_max_1, dec_min, dec_max]]
           else:
               bounds = [[ra_min, ra_max, dec_min, dec_max]]
        return bounds
 
    def calculate_targets(self, subarray, target_name, ra_deg, dec_deg, fecenter, obsid):
        """Calculates and communicates targets within the current field of view
        for downstream processes.

        Args:
            subarray (str): The name of the current subarray.  
            (TODO: use this to manage multiple simultaneous subarrays). 
            target_name (str): The name of the primary pointing target.
            ra_deg (float): RA in degrees of the primary pointing target. 
            J2000 coordinates should be used if the original 26M Gaia 
            DR2-derived star list is used.
            dec_deg (float): As above, Dec in degrees. 
            fecenter (float): The centre frequency of the current observation,
            in MHz.
            (TODO: more nuanced estimate of field of view).
            obsid (str): `OBSID` (unique identifier) for the current obs. Note
            that `OBSID` is of the form:
            `<telescope name>:<subarray name>:<PKTSTART timestamp>`

        Returns:
            None   
        """
        log.info('Calculating for {} at ({}, {})'.format(target_name, ra_deg, dec_deg))
        # Calculate beam radius (TODO: generalise for other antennas besides MeerKAT):
        beam_radius = 0.5*(constants.c/(fecenter*1e6))/25.0         
        log.info('Applying bounding box.')
        bounds = self.query_bounds(np.deg2rad(ra_deg), np.deg2rad(dec_deg), beam_radius)
        # Building SQL query:
        if(len(bounds) > 1):
            box_1 = np.rad2deg(bounds[0])
            box_2 = np.rad2deg(bounds[1])
            box_query = """ 
                        SELECT `source_id`, `ra`, `decl`, `dist_c`
                        FROM target_list
                        WHERE ((`ra` > {} AND `ra` < {}) OR (`ra` > {} AND `ra` < {}))
                        AND (`decl` > {} AND `decl` < {})
                        """.format(box_1[0], box_1[1], box_2[0], box_2[1], box_1[2], box_1[3])
        elif(len(bounds) == 1):      
            box = np.rad2deg(bounds[0])
            box_query = """ 
                        SELECT `source_id`, `ra`, `decl`, `dist_c`
                        FROM target_list
                        WHERE (`ra` > {} AND `ra` < {})
                        AND (`decl` > {} AND `decl` < {})
                        """.format(box[0], box[1], box[2], box[3])
        else:
            box_query = """
                        SELECT `source_id`, `ra`, `decl`, `dist_c`
                        FROM target_list
                        """
        targets_query = """
                        SELECT `source_id`, `ra`, `decl`, `dist_c`
                        FROM ({}) as T
                        WHERE ACOS(SIN(RADIANS(`decl`))*SIN({})+COS(RADIANS(`decl`))*COS({})*COS({}-RADIANS(`ra`)))<{};
                        """.format(box_query, np.deg2rad(dec_deg), np.deg2rad(dec_deg), np.deg2rad(ra_deg), beam_radius)
        debug_query = """
                        SELECT `source_id`, `ra`, `decl`, `dist_c`
                        FROM ({}) as T
                      """.format(box_query)
        #start_ts = time.time()
        #target_debug = pd.read_sql(debug_query, con=self.connection)
        #end_ts = time.time()
        #log.info('Debug retrieval {} of view in {} seconds'.format(target_debug.shape[0], int(end_ts - start_ts)))
        start_ts = time.time()
        with self.engine.begin() as connection:
            target_list = pd.read_sql(targets_query, con=connection)
       # self.session.commit()
        #print(targets_query)
        end_ts = time.time()
        log.info('Retrieved {} targets in field of view in {} seconds'.format(target_list.shape[0], int(end_ts - start_ts)))
        pointing_dict = {'source_id':target_name, 'ra':ra_deg, 'dec':dec_deg}
        json_list = self.format_targets(target_list, pointing_dict)
        #json_list = self.debug_list()
        # Write the list of targets to Redis under OBSID and alert listeners
        # that new targets are available:
        self.redis_server.set('targets:{}'.format(obsid), json_list)
        self.redis_server.publish(self.targets_channel, 'targets:{}'.format(obsid))

    def debug_list(self):
        """Return a debug list of targets.
        """
        target_list = []
        for i in range(100):
            source_i = {}
            source_i['source_id'] = f'test_source_{i}'
            source_i['ra'] = 0.0
            source_i['dec'] = 0.0
            target_list.append(source_i)
        json_list = json.dumps(target_list)
        return(json_list)

    def format_targets(self, df, pointing_dict):
        """Formats dataframe target list into JSON list of dict for storing in Redis. 
        
        Args:
            df (dataframe): Dataframe of target list for the current pointing.
            pointing_dict (dict): Dictionary containing the name of the 
            primary pointing and its coordinates.

        Returns:
            json_list (JSON): JSON-formatted dictionary containing the targets
            in the current field of view. The structure is as follows:
            `[{primary_pointing, primary_ra, primary_dec}, {source_id_0, ra, 
            dec}, {source_id_1, ra, dec}, ... ]`
        """ 
        output_list = [pointing_dict]
        df = df.to_numpy()
        for i in range(df.shape[0]):
            source_i = {}
            source_i['source_id'] = df[i, 0]
            source_i['ra'] = df[i, 1]
            source_i['dec'] = df[i, 2]
            output_list.append(source_i)
        json_list = json.dumps(output_list)
        return(json_list)

